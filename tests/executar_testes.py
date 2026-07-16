#!/usr/bin/env python3
"""
Checklist de teste por cenário — PEO-BD (Etapa 3 da infra de teste de stress)
Roda, contra uma API já de pé (local ou deployada), a bateria de testes de
performance, corretude e capacidade descrita no checklist do projeto, após o
seed de stress (scripts/seed_stress_test.py) ter rodado. Registra o tempo de
resposta de cada cenário e gera um relatório de aprovação/reprovação.

Uso:
    python scripts/executar_testes.py
    python scripts/executar_testes.py --base-url https://projeto-bd-one.vercel.app
    python scripts/executar_testes.py --periodo ano --output data/relatorios/stress.txt

Observação: não existe endpoint GET /api/rastreio — a listagem equivalente
é GET /api/remessas, usada nos testes T02/T09/T10. A API exige JWT em toda
rota /api/* (exceto login/ping) — o script faz login com o usuário demo
"erick" (admin, enxerga os dois CDs) antes de rodar o checklist.
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

LOGIN_DEMO = {"usuario": "erick", "senha": "***REDACTED***"}


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/auth/login", json=LOGIN_DEMO)
    resp.raise_for_status()
    return resp.json()["token"]


class Resultado:
    def __init__(self, codigo: str, categoria: str, descricao: str):
        self.codigo = codigo
        self.categoria = categoria
        self.descricao = descricao
        self.passou = False
        self.detalhe = ""
        self.erro: str | None = None


async def _get_json(client: httpx.AsyncClient, path: str, **params) -> tuple[dict | list, float]:
    inicio = time.perf_counter()
    resp = await client.get(path, params=params)
    dt = time.perf_counter() - inicio
    resp.raise_for_status()
    return resp.json(), dt


# ── Cenários ──────────────────────────────────────────────────────────────────

async def t01(client):
    r, t = await _get_json(client, "/api/dashboard")
    return t < 3.0, f"{t:.2f}s"

async def t02(client):
    r, t = await _get_json(client, "/api/remessas", limit=100)
    return t < 2.0, f"{t:.2f}s ({len(r)} registros)"

async def t03(client):
    r, t = await _get_json(client, "/api/ondas/historico", periodo="mes")
    return t < 4.0, f"{t:.2f}s"

async def t04(client):
    r, t = await _get_json(client, "/api/transportadoras/estatisticas")
    return t < 3.0, f"{t:.2f}s"

async def t05(client):
    r, t = await _get_json(client, "/api/dashboard")
    otif = r.get("otif_pct", 0)
    return 85.0 <= otif <= 100.0, f"OTIF={otif}%"

async def t06(client):
    r, t = await _get_json(client, "/api/dashboard")
    total = sum(r.get("remessas", {}).values())
    return total > 0, f"total={total}"

async def t07(client):
    r, t = await _get_json(client, "/api/transportadoras/estatisticas")
    com_historico = sum(1 for x in r if x.get("total_programacoes", 0) + x.get("total_erros", 0) > 0)
    return com_historico > 0, f"{com_historico}/{len(r)} transportadora(s) com histórico"

async def t08(client):
    r, t = await _get_json(client, "/api/dashboard")
    critico = r.get("alertas", {}).get("critica")
    ok = isinstance(critico, int) and critico >= 0
    return ok, f"críticos ativos={critico}"

async def t09(client):
    r, t = await _get_json(client, "/api/remessas", limit=1000)
    return len(r) == 1000, f"{len(r)} registro(s) retornado(s)"

async def t10(client):
    transportadoras, _ = await _get_json(client, "/api/transportadoras")
    if not transportadoras:
        return False, "nenhuma transportadora cadastrada"
    tid = transportadoras[0]["id"]
    todas, _ = await _get_json(client, "/api/remessas", limit=2000)
    subset, t = await _get_json(client, "/api/remessas", limit=2000, transportadora_id=tid)
    ok = 0 < len(subset) <= len(todas)
    return ok, f"{len(subset)}/{len(todas)} remessa(s) (transportadora {tid})"

async def t11(client):
    r, t = await _get_json(client, "/api/ondas/historico", periodo="ano")
    return t < 5.0, f"{t:.2f}s"

async def t12(client):
    inicio = time.perf_counter()
    resp = await client.get("/api/relatorio/pdf")
    t = time.perf_counter() - inicio
    ok = resp.status_code == 200 and resp.content[:4] == b"%PDF"
    return ok, f"{t:.2f}s, {len(resp.content)} bytes"


CHECKLIST = [
    ("T01", "Performance", "GET /api/dashboard — tempo < 3s", t01),
    ("T02", "Performance", "GET /api/remessas?limit=100 — tempo < 2s", t02),
    ("T03", "Performance", "GET /api/ondas/historico?periodo=mes — tempo < 4s", t03),
    ("T04", "Performance", "GET /api/transportadoras/estatisticas — tempo < 3s", t04),

    ("T05", "Corretude", "OTIF (dashboard) entre 85% e 100%", t05),
    ("T06", "Corretude", "Total de remessas > 0", t06),
    ("T07", "Corretude", "Transportadoras com histórico > 0", t07),
    ("T08", "Corretude", "Alertas críticos ativos >= 0", t08),

    ("T09", "Capacidade", "GET /api/remessas?limit=1000 — retorna 1000 sem erro", t09),
    ("T10", "Capacidade", "Filtro por transportadora retorna subset correto", t10),
    ("T11", "Capacidade", "GET /api/ondas/historico?periodo=ano — tempo < 5s", t11),
    ("T12", "Capacidade", "PDF de ondas gerado sem timeout", t12),
]


async def rodar_checklist(base_url: str) -> list[Resultado]:
    resultados = []
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        token = await _login(client)
        client.headers["Authorization"] = f"Bearer {token}"
        for codigo, categoria, descricao, fn in CHECKLIST:
            resultado = Resultado(codigo, categoria, descricao)
            try:
                passou, detalhe = await fn(client)
                resultado.passou = passou
                resultado.detalhe = detalhe
            except Exception as e:
                resultado.passou = False
                resultado.erro = f"{type(e).__name__}: {e}"
            resultados.append(resultado)
            status = "OK" if resultado.passou else "FALHOU"
            print(f"    {codigo} [{status}] {descricao}")
    return resultados


def montar_relatorio(resultados: list[Resultado], base_url: str, periodo_label: str) -> str:
    linhas = []
    linhas.append("RELATÓRIO DE STRESS TEST — PEO-BD")
    linhas.append(f"Período: {periodo_label} | URL: {base_url}")
    linhas.append(f"Executado em: {datetime.now():%d/%m/%Y %H:%M:%S}")
    linhas.append("")

    categoria_atual = None
    for r in resultados:
        if r.categoria != categoria_atual:
            categoria_atual = r.categoria
            linhas.append(f"── {categoria_atual} ──")
        icone = "✅" if r.passou else "❌"
        info = r.erro if r.erro else r.detalhe
        extra = "" if r.passou else "  (FALHOU)"
        linhas.append(f"{r.codigo} {icone} {r.descricao}: {info}{extra}")
    linhas.append("")

    total = len(resultados)
    passaram = sum(1 for r in resultados if r.passou)
    falhas = [r.codigo for r in resultados if not r.passou]

    linhas.append(f"RESULTADO: {passaram}/{total} testes passaram")
    if falhas:
        linhas.append(f"GARGALOS IDENTIFICADOS: {', '.join(falhas)}")
    else:
        linhas.append("Nenhum gargalo identificado.")

    return "\n".join(linhas)


async def main():
    parser = argparse.ArgumentParser(description="Executa o checklist de stress test do PEO-BD")
    parser.add_argument("--base-url", default="http://localhost:8000",
                         help="URL base da API (default: http://localhost:8000)")
    parser.add_argument("--periodo", default="não informado",
                         help="Rótulo do período seedado, só para o relatório (ex.: ano, semestre)")
    parser.add_argument("--output", default=None,
                         help="Caminho do arquivo onde salvar o relatório (opcional)")
    args = parser.parse_args()

    print(f"  Executando checklist contra: {args.base_url}\n")
    resultados = await rodar_checklist(args.base_url)

    relatorio = montar_relatorio(resultados, args.base_url, args.periodo)
    print("\n" + relatorio)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(relatorio, encoding="utf-8")
        print(f"\nRelatório salvo em: {out_path}")

    falhou_algum = any(not r.passou for r in resultados)
    sys.exit(1 if falhou_algum else 0)


if __name__ == "__main__":
    asyncio.run(main())
