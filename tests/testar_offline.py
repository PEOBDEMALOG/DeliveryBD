"""
scripts/testar_offline.py — CORREÇÃO 9

Suite de validação automatizada do Modo de Contingência (offline) do PEO-BD.

Sobe uma instância isolada do backend (SQLite dedicado em pasta temporária,
porta própria) e dirige um Chromium real via Playwright contra ela. As
"quedas" de conexão são simuladas de duas formas, escolhidas conforme o que
cada cenário precisa validar:

  - derrubando/subindo o processo uvicorn de verdade, para exercitar o
    mecanismo real de detecção do front (setInterval de 15s em /api/ping,
    3 falhas consecutivas → entrarEmContingencia — ver
    frontend/index.html:3407-3453);
  - interceptando uma rota específica com Playwright (page.route), quando o
    cenário precisa que só uma parte das chamadas falhe no meio de uma
    sequência (cenário O06).

Instalação (não faz parte do config/requirements.txt — só é necessário para rodar
esta suíte manualmente):
    pip install playwright httpx
    playwright install chromium

Uso:
    python scripts/testar_offline.py
    python scripts/testar_offline.py --cenario O03 O04
    python scripts/testar_offline.py --headed

O suíte inteiro roda em ~5-8 minutos, pois vários cenários precisam esperar
o ciclo real de detecção de queda (3 × 15s) e reconexão do front. Gera um
relatório em texto no console e em data/outputs/testes_offline/.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    # Console do Windows usa cp1252 por padrão — não dá conta dos emojis/setas do relatório.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import httpx
except ImportError:
    print("httpx não instalado. Rode: pip install httpx")
    raise SystemExit(1)

try:
    from openpyxl import Workbook
except ImportError:
    print("openpyxl não instalado (já é dependência do projeto). Rode: pip install openpyxl")
    raise SystemExit(1)

try:
    from playwright.async_api import (
        BrowserContext,
        Download,
        Page,
        Route,
        TimeoutError as PWTimeoutError,
        async_playwright,
    )
except ImportError:
    print("Playwright não instalado. Rode: pip install playwright && playwright install chromium")
    raise SystemExit(1)


BASE_DIR = Path(__file__).resolve().parent.parent
RELATORIOS_DIR = BASE_DIR / "data" / "outputs" / "testes_offline"

USUARIO = "erick"
# SOMENTE TESTE LOCAL, NUNCA USAR EM DEPLOY — casa com o AUTH_SENHA_ERICK
# injetado em _env() abaixo pro servidor isolado que esta suíte sobe.
SENHA = "SOMENTE-TESTE-LOCAL-NUNCA-USAR-EM-DEPLOY"

PING_INTERVALO_S = 15  # deve casar com o setInterval de iniciarMonitoramentoConexao (front)
FALHAS_PARA_ENTRAR = 3  # falhasConsecutivas >= 3 -> entrarEmContingencia()
TIMEOUT_ENTRAR_S = FALHAS_PARA_ENTRAR * PING_INTERVALO_S + 20
TIMEOUT_SAIR_S = PING_INTERVALO_S + 20


CENARIOS = [
    {
        "id": "O01",
        "nome": "Queda imediata — IndexedDB vazio",
        "descricao": "Simula queda com nenhum dado local. Verifica se o banner aparece e a tela de contingência carrega.",
        "validar": ["banner_visivel", "tela_offline_carrega", "formulario_funcional"],
    },
    {
        "id": "O02",
        "nome": "Queda com dados pendentes — sincronização parcial",
        "descricao": "Simula queda após 3 remessas adicionadas localmente. Verifica sincronização ao reconectar.",
        "validar": ["dados_no_indexeddb", "sincroniza_ao_reconectar", "sem_duplicacao"],
    },
    {
        "id": "O03",
        "nome": "Upload de planilha offline",
        "descricao": "Verifica se SheetJS processa corretamente SAP e UPS em modo offline.",
        "validar": ["sap_processado", "ups_processado", "remessas_no_indexeddb"],
    },
    {
        "id": "O04",
        "nome": "PDF gerado offline",
        "descricao": "Verifica se jsPDF gera PDF com remessas locais sem chamar o servidor.",
        "validar": ["pdf_baixado", "pdf_contem_remessas"],
    },
    {
        "id": "O05",
        "nome": "Reconexão com conflito",
        "descricao": "Simula remessa criada offline com mesmo número que já existe no banco.",
        "validar": ["deduplicacao_por_hash", "sem_erro_500", "alerta_conflito_visivel"],
    },
    {
        "id": "O06",
        "nome": "Queda durante sincronização",
        "descricao": "Simula queda no meio da sincronização. Verifica se continua de onde parou.",
        "validar": ["sincronizacao_retomada", "sem_dados_perdidos"],
    },
]

# Validações não-bloqueantes: reprovar apenas rebaixa o cenário para ⚠, não para ❌.
AVISOS = {
    "O05": {"alerta_conflito_visivel"},
}


@dataclass
class ResultadoCenario:
    id: str
    nome: str
    checks: dict = field(default_factory=dict)
    detalhe: str = ""
    erro: str | None = None
    duracao_s: float = 0.0

    def status(self) -> str:
        if self.erro:
            return "❌"
        if not self.checks:
            return "❌"
        avisos = AVISOS.get(self.id, set())
        criticas = {k: v for k, v in self.checks.items() if k not in avisos}
        avisadas = {k: v for k, v in self.checks.items() if k in avisos}
        if not all(criticas.values()):
            return "❌"
        if avisadas and not all(avisadas.values()):
            return "⚠"
        return "✅"


class ServidorTeste:
    """Sobe/derruba a instância uvicorn usada pelos testes, com um banco
    SQLite isolado — para não tocar nos dados de desenvolvimento."""

    def __init__(self, porta: int, db_path: Path):
        self.porta = porta
        self.db_path = db_path
        self.base_url = f"http://127.0.0.1:{porta}"
        self.proc: subprocess.Popen | None = None

    def _env(self) -> dict:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        # SOMENTE TESTE LOCAL, NUNCA USAR EM DEPLOY — valor fixo só pra essa
        # suíte conseguir assinar/validar tokens contra o SQLite isolado
        # acima; não é (e não deve virar) o segredo real de nenhum ambiente.
        env.setdefault("JWT_SECRET", "SOMENTE-TESTE-LOCAL-NUNCA-USAR-EM-DEPLOY")
        # Idem para as senhas — valores fixos só pro servidor isolado acima;
        # timoteo/carlos não fazem login nesta suíte, só erick, mas o
        # servidor exige as 3 vars pra subir (core/config.py).
        env.setdefault("AUTH_SENHA_TIMOTEO", "SOMENTE-TESTE-LOCAL-NUNCA-USAR-EM-DEPLOY")
        env.setdefault("AUTH_SENHA_CARLOS", "SOMENTE-TESTE-LOCAL-NUNCA-USAR-EM-DEPLOY")
        env.setdefault("AUTH_SENHA_ERICK", SENHA)
        # Trava de segurança: essa suíte só pode rodar contra o SQLite
        # isolado que ela mesma cria acima — nunca contra um Postgres real
        # (dev, teste ou produção). Se algo sobrescrever DATABASE_URL depois
        # daqui, prefira quebrar alto a rodar os cenários offline (que
        # derrubam/sobem o servidor e mexem em dados) contra um banco real.
        assert env["DATABASE_URL"].startswith("sqlite"), (
            "testar_offline.py só pode rodar contra SQLite isolado — "
            f"DATABASE_URL atual: {env['DATABASE_URL']!r}"
        )
        return env

    def iniciar(self):
        if self.proc and self.proc.poll() is None:
            return
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "api.main:app",
                "--host", "127.0.0.1", "--port", str(self.porta),
                "--log-level", "warning",
            ],
            cwd=str(BASE_DIR),
            env=self._env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )

    def parar(self):
        if not self.proc:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.proc = None

    async def aguardar_no_ar(self, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient() as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(f"{self.base_url}/api/ping", timeout=2)
                    if r.status_code == 200:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        return False


async def obter_token(base_url: str) -> str:
    """/api/remessas e /api/remessas/manual exigem JWT (ver exigir_jwt em api/main.py) —
    as chamadas httpx diretas do script (fora do browser) precisam desse token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base_url}/api/auth/login", json={"usuario": USUARIO, "senha": SENHA})
        resp.raise_for_status()
        return resp.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Helpers de browser ───────────────────────────────────────────────────────

async def login(page: Page, base_url: str):
    await page.goto(base_url, wait_until="domcontentloaded")
    await page.fill('input[autocomplete="username"]', USUARIO)
    await page.fill('input[autocomplete="current-password"]', SENHA)
    await page.click('button:has-text("Entrar")')
    await page.wait_for_selector('button:has-text("Modo Offline")', timeout=15000)


async def abrir_pagina_offline(page: Page):
    await page.click('button:has-text("Modo Offline")')
    await page.wait_for_selector("text=Carregar Planilha do Backlog", timeout=5000)


async def banner_contingencia_visivel(page: Page) -> bool:
    loc = page.locator("text=Modo de Contingência — Sistema operando localmente")
    try:
        return await loc.is_visible()
    except Exception:
        return False


async def aguardar_estado_banner(page: Page, esperado: bool, timeout_s: float) -> tuple[bool, float]:
    inicio = time.monotonic()
    while time.monotonic() - inicio < timeout_s:
        if await banner_contingencia_visivel(page) == esperado:
            return True, time.monotonic() - inicio
        await asyncio.sleep(1)
    return False, time.monotonic() - inicio


async def salvar_remessa_local(page: Page, remessa: dict):
    await page.evaluate("(r) => window.salvarRemessaLocal(r)", remessa)


async def contar_pendentes_indexeddb(page: Page) -> dict:
    return await page.evaluate("() => window.contarPendentes()")


async def numeros_pendentes_indexeddb(page: Page) -> list[str]:
    return await page.evaluate(
        "async () => (await window.listarRemessasLocaisNaoSincronizadas()).map(r => r.numero)"
    )


def _criar_planilha(path: Path, cabecalho: list[str], linhas: list[dict]):
    wb = Workbook()
    ws = wb.active
    ws.append(cabecalho)
    for linha in linhas:
        ws.append([linha.get(col, "") for col in cabecalho])
    wb.save(path)


def criar_planilha_sap(path: Path, linhas: list[dict]):
    _criar_planilha(path, ["Remessa", "Cliente", "Cidade", "Vol (m³)", "Peso (kg)", "Valor NF"], linhas)


def criar_planilha_ups(path: Path, linhas: list[dict]):
    _criar_planilha(path, ["ID_UPS", "Destinatário", "UF", "Volume (m³)", "Peso", "Valor NF"], linhas)


# ── Cenários ─────────────────────────────────────────────────────────────────

async def cenario_O01(novo_contexto, servidor: ServidorTeste, **_) -> ResultadoCenario:
    r = ResultadoCenario(id="O01", nome="Queda imediata — IndexedDB vazio")
    t0 = time.monotonic()
    context: BrowserContext = await novo_contexto()
    page = await context.new_page()
    try:
        await login(page, servidor.base_url)
        # Contexto de browser novo -> IndexedDB começa vazio, sem seed necessário.
        servidor.parar()
        banner_ok, dt = await aguardar_estado_banner(page, True, TIMEOUT_ENTRAR_S)
        r.checks["banner_visivel"] = banner_ok
        r.detalhe = f"banner em {dt:.0f}s"

        try:
            await abrir_pagina_offline(page)
            r.checks["tela_offline_carrega"] = True
        except PWTimeoutError:
            r.checks["tela_offline_carrega"] = False

        try:
            await page.locator("text=Arraste ou clique para selecionar a planilha").wait_for(
                state="visible", timeout=3000
            )
            r.checks["formulario_funcional"] = True
        except PWTimeoutError:
            r.checks["formulario_funcional"] = False
    finally:
        servidor.iniciar()
        await servidor.aguardar_no_ar()
        with contextlib.suppress(Exception):
            await aguardar_estado_banner(page, False, TIMEOUT_SAIR_S)
        await context.close()
        r.duracao_s = time.monotonic() - t0
    return r


async def cenario_O02(novo_contexto, servidor: ServidorTeste, **_) -> ResultadoCenario:
    r = ResultadoCenario(id="O02", nome="Queda com dados pendentes — sincronização parcial")
    t0 = time.monotonic()
    context = await novo_contexto()
    page = await context.new_page()
    prefixo = f"OFF-O02-{uuid.uuid4().hex[:6]}"
    numeros = [f"{prefixo}-{i}" for i in range(1, 4)]
    try:
        token = await obter_token(servidor.base_url)
        await login(page, servidor.base_url)
        servidor.parar()
        banner_ok, _ = await aguardar_estado_banner(page, True, TIMEOUT_ENTRAR_S)
        if not banner_ok:
            r.erro = "sistema não entrou em contingência dentro do prazo"
            return r

        for n in numeros:
            await salvar_remessa_local(page, {
                "numero": n, "cliente": "Cliente Teste O02", "cidade": "Osasco",
                "volume": 5.0, "peso": 80.0, "transportadora": "",
                "status": "novo", "origem": "manual_contingencia",
            })
        pendentes = await contar_pendentes_indexeddb(page)
        r.checks["dados_no_indexeddb"] = pendentes["remessas"] == 3

        servidor.iniciar()
        await servidor.aguardar_no_ar()
        sync_ok, dt = await aguardar_estado_banner(page, False, TIMEOUT_SAIR_S)
        await page.wait_for_timeout(1500)  # dá um instante para o loop sequencial de sync terminar

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{servidor.base_url}/api/remessas", params={"limit": 500}, headers=_auth(token))
            servidor_nums = [x["numero"] for x in resp.json()]
        encontrados = [n for n in numeros if n in servidor_nums]
        duplicados = len(servidor_nums) - len(set(servidor_nums))

        r.checks["sincroniza_ao_reconectar"] = sync_ok and len(encontrados) == 3
        r.checks["sem_duplicacao"] = duplicados == 0
        r.detalhe = f"{len(encontrados)}/3 sincronizadas em {dt:.0f}s, {duplicados} duplicata(s)"
    finally:
        await context.close()
        r.duracao_s = time.monotonic() - t0
    return r


async def cenario_O03(novo_contexto, servidor: ServidorTeste, tmp_dir: Path, **_) -> ResultadoCenario:
    r = ResultadoCenario(id="O03", nome="Upload de planilha offline")
    t0 = time.monotonic()
    context = await novo_contexto()
    page = await context.new_page()
    try:
        await login(page, servidor.base_url)
        await abrir_pagina_offline(page)

        sap_path = tmp_dir / f"sap_{uuid.uuid4().hex[:6]}.xlsx"
        ups_path = tmp_dir / f"ups_{uuid.uuid4().hex[:6]}.xlsx"
        sap_num = f"SAP-{uuid.uuid4().hex[:6]}"
        ups_num = f"UPS-{uuid.uuid4().hex[:6]}"
        criar_planilha_sap(sap_path, [{
            "Remessa": sap_num, "Cliente": "Cliente SAP", "Cidade": "Osasco",
            "Vol (m³)": 3.2, "Peso (kg)": 40, "Valor NF": 1500,
        }])
        criar_planilha_ups(ups_path, [{
            "ID_UPS": ups_num, "Destinatário": "Cliente UPS", "UF": "SC",
            "Volume (m³)": 2.1, "Peso": 25, "Valor NF": 900,
        }])

        arquivo = page.locator('input[x-ref="fileInputContingencia"]')

        await arquivo.set_input_files(str(sap_path))
        await page.wait_for_timeout(800)
        pendentes_sap = await numeros_pendentes_indexeddb(page)
        r.checks["sap_processado"] = sap_num in pendentes_sap

        await arquivo.set_input_files(str(ups_path))
        await page.wait_for_timeout(800)
        pendentes_ups = await numeros_pendentes_indexeddb(page)
        r.checks["ups_processado"] = ups_num in pendentes_ups

        r.checks["remessas_no_indexeddb"] = sap_num in pendentes_ups and ups_num in pendentes_ups
        r.detalhe = (
            f"SAP={'ok' if r.checks['sap_processado'] else 'falhou'}, "
            f"UPS={'ok' if r.checks['ups_processado'] else 'falhou'}"
        )
    finally:
        await context.close()
        r.duracao_s = time.monotonic() - t0
    return r


async def cenario_O04(novo_contexto, servidor: ServidorTeste, tmp_dir: Path, **_) -> ResultadoCenario:
    r = ResultadoCenario(id="O04", nome="PDF gerado offline")
    t0 = time.monotonic()
    context = await novo_contexto()
    page = await context.new_page()
    try:
        await login(page, servidor.base_url)
        await abrir_pagina_offline(page)

        num1, num2 = f"PDF-{uuid.uuid4().hex[:6]}", f"PDF-{uuid.uuid4().hex[:6]}"
        for n in (num1, num2):
            await salvar_remessa_local(page, {
                "numero": n, "cliente": "Cliente PDF", "cidade": "Itajaí",
                "volume": 1.5, "peso": 20, "transportadora": "UPS",
                "status": "novo", "origem": "manual_contingencia",
            })
        await page.evaluate(
            "() => window.Alpine.$data(document.querySelector('[x-data]')).carregarRemessasLocais()"
        )
        await page.wait_for_timeout(300)

        chamadas_api = []
        page.on("request", lambda req: chamadas_api.append(req.url) if "/api/" in req.url else None)

        async with page.expect_download() as dl_info:
            await page.click('button:has-text("Imprimir Onda (PDF)")')
        download: Download = await dl_info.value
        destino = tmp_dir / f"onda_o04_{uuid.uuid4().hex[:6]}.pdf"
        await download.save_as(str(destino))

        conteudo = destino.read_bytes()
        r.checks["pdf_baixado"] = destino.exists() and conteudo.startswith(b"%PDF") and len(conteudo) > 500
        texto = conteudo.decode("latin-1", errors="ignore")
        r.checks["pdf_contem_remessas"] = num1 in texto and num2 in texto
        r.detalhe = f"{len(conteudo) / 1024:.1f} KB, chamadas ao servidor durante geração: {len(chamadas_api)}"
    finally:
        await context.close()
        r.duracao_s = time.monotonic() - t0
    return r


async def cenario_O05(novo_contexto, servidor: ServidorTeste, **_) -> ResultadoCenario:
    r = ResultadoCenario(id="O05", nome="Reconexão com conflito")
    t0 = time.monotonic()
    numero = f"CONFLITO-{uuid.uuid4().hex[:6]}"
    volume, peso = 12.0, 300.0
    token = await obter_token(servidor.base_url)

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{servidor.base_url}/api/remessas/manual", json={
            "numero_remessa": numero, "cliente_nome": "Cliente Conflito",
            "cidade": "Osasco", "volume_m3": volume, "peso_kg": peso,
            "transportadora_nome": "DHL",
        }, headers=_auth(token))
        criada_previamente = resp.status_code == 200

    context = await novo_contexto()
    page = await context.new_page()
    respostas_500 = []
    page.on("response", lambda res: respostas_500.append(res.url) if res.status == 500 else None)
    try:
        await login(page, servidor.base_url)
        servidor.parar()
        banner_ok, _ = await aguardar_estado_banner(page, True, TIMEOUT_ENTRAR_S)
        if not banner_ok:
            r.erro = "sistema não entrou em contingência dentro do prazo"
            return r

        await salvar_remessa_local(page, {
            "numero": numero, "cliente": "Cliente Conflito", "cidade": "Osasco",
            "volume": volume, "peso": peso, "transportadora": "DHL",
            "status": "novo", "origem": "manual_contingencia",
        })

        servidor.iniciar()
        await servidor.aguardar_no_ar()
        await aguardar_estado_banner(page, False, TIMEOUT_SAIR_S)
        await page.wait_for_timeout(1500)

        pendentes = await contar_pendentes_indexeddb(page)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{servidor.base_url}/api/remessas", params={"limit": 500}, headers=_auth(token))
            ocorrencias = [x for x in resp.json() if x["numero"] == numero]

        r.checks["deduplicacao_por_hash"] = (
            criada_previamente and len(ocorrencias) == 1 and pendentes["remessas"] == 0
        )
        r.checks["sem_erro_500"] = len(respostas_500) == 0

        # page.locator("text=...").count() contaria também ocorrências escondidas
        # em outras páginas da SPA que nunca saem do DOM (x-show só troca
        # display:none) — ex.: o card "Duplicatas" do Painel de Diagnóstico.
        # Por isso o check varre só elementos realmente visíveis na tela.
        alerta = await page.evaluate(
            """() => {
                const termos = /conflito|duplicat/i;
                return Array.from(document.querySelectorAll('body *')).some(el => {
                    if (el.children.length > 0) return false;
                    if (!termos.test(el.textContent || '')) return false;
                    if (el.getClientRects().length === 0) return false;
                    const estilo = window.getComputedStyle(el);
                    return estilo.display !== 'none' && estilo.visibility !== 'hidden';
                });
            }"""
        )
        r.checks["alerta_conflito_visivel"] = bool(alerta)
        r.detalhe = (
            f"{len(ocorrencias)} ocorrência(s) no servidor, "
            f"alerta visual: {'sim' if r.checks['alerta_conflito_visivel'] else 'não'}"
        )
    finally:
        await context.close()
        r.duracao_s = time.monotonic() - t0
    return r


async def cenario_O06(novo_contexto, servidor: ServidorTeste, **_) -> ResultadoCenario:
    r = ResultadoCenario(id="O06", nome="Queda durante sincronização")
    t0 = time.monotonic()
    context = await novo_contexto()
    page = await context.new_page()
    prefixo = f"OFF-O06-{uuid.uuid4().hex[:6]}"
    numeros = [f"{prefixo}-{i}" for i in range(1, 5)]
    try:
        token = await obter_token(servidor.base_url)
        await login(page, servidor.base_url)
        servidor.parar()
        banner_ok, _ = await aguardar_estado_banner(page, True, TIMEOUT_ENTRAR_S)
        if not banner_ok:
            r.erro = "sistema não entrou em contingência dentro do prazo"
            return r

        for n in numeros:
            await salvar_remessa_local(page, {
                "numero": n, "cliente": "Cliente O06", "cidade": "Osasco",
                "volume": 4.0, "peso": 60.0, "transportadora": "",
                "status": "novo", "origem": "manual_contingencia",
            })

        # Deixa passar só a 1ª chamada de sync; as demais falham — simula queda
        # de conexão bem no meio do loop sequencial de sincronizarDadosLocais(),
        # sem derrubar /api/ping (que segue de pé e é o que dispara
        # sairDaContingencia() -> sincronizarDadosLocais()).
        contador = {"n": 0}

        async def rota_meio_sync(route: Route):
            contador["n"] += 1
            if contador["n"] == 1:
                await route.continue_()
            else:
                await route.abort("failed")

        await page.route("**/api/remessas/manual", rota_meio_sync)

        servidor.iniciar()
        await servidor.aguardar_no_ar()
        await aguardar_estado_banner(page, False, TIMEOUT_SAIR_S)
        await page.wait_for_timeout(2000)

        pendentes_apos_queda = await contar_pendentes_indexeddb(page)

        await page.unroute("**/api/remessas/manual")

        # Sem nenhuma nova ação do usuário/servidor: confirma se o app
        # reprocessa sozinho o restante da fila.
        await page.wait_for_timeout(PING_INTERVALO_S * 1000 + 5000)
        pendentes_sem_retry = await contar_pendentes_indexeddb(page)
        retomou_sozinho = pendentes_sem_retry["remessas"] < pendentes_apos_queda["remessas"]

        # Força um novo ciclo offline -> online (o único jeito, hoje, de
        # reprocessar a fila) para confirmar que os dados não foram perdidos.
        servidor.parar()
        await aguardar_estado_banner(page, True, TIMEOUT_ENTRAR_S)
        servidor.iniciar()
        await servidor.aguardar_no_ar()
        await aguardar_estado_banner(page, False, TIMEOUT_SAIR_S)
        await page.wait_for_timeout(1500)

        pendentes_final = await contar_pendentes_indexeddb(page)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{servidor.base_url}/api/remessas", params={"limit": 500}, headers=_auth(token))
            servidor_nums = {x["numero"] for x in resp.json()}
        no_servidor = sum(1 for n in numeros if n in servidor_nums)

        r.checks["sincronizacao_retomada"] = retomou_sozinho
        r.checks["sem_dados_perdidos"] = pendentes_final["remessas"] == 0 and no_servidor == len(numeros)
        r.detalhe = (
            f"{pendentes_apos_queda['remessas']} pendente(s) após a queda parcial, "
            f"retomou sozinho: {'sim' if retomou_sozinho else 'não'} — "
            f"após novo ciclo: {pendentes_final['remessas']} pendente(s), "
            f"{no_servidor}/{len(numeros)} no servidor"
        )
    finally:
        with contextlib.suppress(Exception):
            await page.unroute("**/api/remessas/manual")
        await context.close()
        r.duracao_s = time.monotonic() - t0
    return r


FUNCOES = {
    "O01": cenario_O01,
    "O02": cenario_O02,
    "O03": cenario_O03,
    "O04": cenario_O04,
    "O05": cenario_O05,
    "O06": cenario_O06,
}


def gerar_relatorio(resultados: list[ResultadoCenario]) -> str:
    linhas = ["RELATÓRIO MODO OFFLINE — PEO-BD", ""]
    aprovados, reprovados = 0, []
    for r in resultados:
        status = r.status()
        if status == "❌":
            reprovados.append(r.id)
        else:
            aprovados += 1
        motivo = r.erro or r.detalhe or ""
        linhas.append(f"{r.id} {status} {r.nome}: {motivo}")

    linhas.append("")
    resumo = f"{aprovados}/{len(resultados)} cenários aprovados."
    if reprovados:
        resumo += f" Correções necessárias: {', '.join(reprovados)}"
    linhas.append(resumo)
    return "\n".join(linhas)


def parse_args():
    p = argparse.ArgumentParser(description="Testes automatizados do Modo de Contingência (offline) — PEO-BD")
    p.add_argument("--cenario", nargs="+", choices=[c["id"] for c in CENARIOS], help="Roda só os cenários informados")
    p.add_argument("--headed", action="store_true", help="Mostra o browser (padrão: headless)")
    p.add_argument("--porta", type=int, default=int(os.getenv("TESTAR_OFFLINE_PORT", "8879")))
    return p.parse_args()


async def main() -> int:
    args = parse_args()
    cenarios_alvo = [c for c in CENARIOS if not args.cenario or c["id"] in args.cenario]

    RELATORIOS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix="peo_offline_"))
    downloads_dir = tmp_root / "downloads"
    downloads_dir.mkdir()
    db_path = tmp_root / "teste_offline.db"

    servidor = ServidorTeste(args.porta, db_path)
    servidor.iniciar()
    if not await servidor.aguardar_no_ar(timeout=30):
        print("Não foi possível subir o servidor de teste.")
        servidor.parar()
        shutil.rmtree(tmp_root, ignore_errors=True)
        return 1

    resultados: list[ResultadoCenario] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=not args.headed)

            async def novo_contexto():
                return await browser.new_context(accept_downloads=True)

            try:
                for c in cenarios_alvo:
                    print(f"→ Rodando {c['id']} — {c['nome']}...")
                    try:
                        resultado = await FUNCOES[c["id"]](novo_contexto, servidor, tmp_dir=downloads_dir)
                    except Exception as e:
                        resultado = ResultadoCenario(id=c["id"], nome=c["nome"], erro=f"{type(e).__name__}: {e}")
                    resultados.append(resultado)
                    print(f"  {resultado.status()} {resultado.detalhe or resultado.erro or ''}")

                    # Garante o servidor de pé antes do próximo cenário, mesmo
                    # que o anterior tenha saído cedo (erro/timeout) com ele parado.
                    if servidor.proc is None or servidor.proc.poll() is not None:
                        servidor.iniciar()
                        await servidor.aguardar_no_ar()
            finally:
                await browser.close()
    finally:
        servidor.parar()
        shutil.rmtree(tmp_root, ignore_errors=True)

    relatorio = gerar_relatorio(resultados)
    print("\n" + relatorio)

    caminho = RELATORIOS_DIR / f"relatorio_offline_{datetime.now():%Y%m%d_%H%M%S}.txt"
    caminho.write_text(relatorio, encoding="utf-8")
    print(f"\nRelatório salvo em {caminho}")

    return 0 if all(r.status() != "❌" for r in resultados) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
