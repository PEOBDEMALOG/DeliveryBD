#!/usr/bin/env python3
"""
Infra de teste de stress — PEO-BD (Etapa 2)
Gera histórico realista de uso diário (Timóteo/Osasco e Carlos/Itajaí):
remessas, planos, ondas, eventos de histórico e alertas, em volume alto,
via bulk insert (nunca linha por linha).

Uso:
    python scripts/seed_stress_test.py --periodo semana --modo adicionar
    python scripts/seed_stress_test.py --periodo ano     --modo limpo

--periodo: semana | mes | trimestre | semestre | nove_meses | ano
--modo:    limpo (apaga dados operacionais antes) | adicionar (mantém o que existe)
"""

import argparse
import asyncio
import random
import sys
import time as time_module
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from core.config import settings, DB_CONNECT_ARGS
from core.models import (
    CentroDistribuicao, Cliente, Transportadora, Veiculo, Upload,
    Remessa, PlanoDia, Onda, OndaRemessa, ProgramacaoColeta,
    HistoricoEventos, Alerta,
)

# ── Parâmetros do período ────────────────────────────────────────────────────

PERIODOS = {
    "semana":      5,
    "mes":        22,
    "trimestre":  66,
    "semestre":  132,
    "nove_meses":198,
    "ano":       264,
}

# Tabelas operacionais apagadas em --modo limpo, em ordem de dependência de FK
# (historico_eventos.remessa_id referencia remessas — precisa vir antes;
#  /api/demo/reset tem a mesma lista mas com essa ordem invertida, um bug
#  latente que só aparece quando historico_eventos.remessa_id está populado).
TABELAS_OPERACIONAIS = [
    "onda_remessas", "programacoes_coleta", "ondas",
    "planos_dia", "alertas", "oportunidades_consolidacao",
    "eventos_rastreio", "historico_eventos", "remessas", "uploads",
]

REGIOES = {
    "OSA": ["capital_sp", "interior_sp", "grande_sp", "litoral_sp", "vale_paraiba"],
    "ITJ": ["sul", "litoral_sc", "planalto_sc", "vale_itajai", "extremo_sul"],
}

# Cadeia de status por que uma remessa passa até chegar no status final —
# cada etapa vira um evento "mudanca_status" no histórico.
FSM_ATE = {
    "novo":            [],
    "planejado":       ["planejado"],
    "coletado":        ["planejado", "coletado"],
    "em_transito":     ["planejado", "coletado", "em_transito"],
    "em_rota_entrega": ["planejado", "coletado", "em_transito", "em_rota_entrega"],
    "entregue":        ["planejado", "coletado", "em_transito", "em_rota_entrega", "entregue"],
    "tentativa":       ["planejado", "coletado", "em_transito", "em_rota_entrega", "tentativa"],
    "devolvido":       ["planejado", "coletado", "em_transito", "em_rota_entrega", "tentativa", "devolvido"],
}
FSM_OFFSET = {
    "planejado":       timedelta(hours=2),
    "coletado":        timedelta(hours=4),
    "em_transito":     timedelta(days=1, hours=1),
    "em_rota_entrega": timedelta(days=1, hours=8),
    "entregue":        timedelta(days=1, hours=10),
    "tentativa":       timedelta(days=1, hours=10),
    "devolvido":       timedelta(days=1, hours=14),
}

ERROS_CODIGOS_TIMEOUT = ["TIMEOUT_SAP", "ARQUIVO_CORROMPIDO"]

CHUNK_DIAS = 44  # dias úteis processados por lote de commit


def dias_uteis(n_dias: int) -> list[date]:
    """Gera n_dias úteis ANTERIORES a hoje (simula histórico real)."""
    dias = []
    d = date.today() - timedelta(days=1)  # começa ontem
    while len(dias) < n_dias:
        if d.weekday() < 5:  # seg-sex
            dias.append(d)
        d -= timedelta(days=1)
    return list(reversed(dias))


def volume_do_dia(dia: date) -> tuple[int, int]:
    """Retorna (n_osa, n_itj) com variação realista."""
    fator = 1.15 if dia.weekday() == 0 else 1.0
    fator *= random.uniform(0.80, 1.20)
    return int(65 * fator), int(35 * fator)


def otif_mensal(mes: int, ano: int) -> float:
    """Simula variação de OTIF ao longo do ano — não linear, tem sazonalidade."""
    if mes in (3, 4, 10, 11):
        return random.uniform(88.0, 93.0)
    elif mes in (12, 1):
        return random.uniform(91.0, 95.0)
    else:
        return random.uniform(93.5, 97.5)


def distribuicao_status(dia: date, hoje: date, otif_alvo: float) -> str:
    """Simula status realista baseado na antiguidade da remessa. O split
    entregue/tentativa/devolvido de remessas já resolvidas é derivado do
    OTIF-alvo do mês (otif_mensal), não fixo — é o que dá sazonalidade real
    ao OTIF do período."""
    dias_passados = (hoje - dia).days
    if dias_passados == 0:
        return random.choices(["novo", "planejado"], weights=[30, 70])[0]
    elif dias_passados <= 2:
        return random.choices(
            ["em_transito", "em_rota_entrega", "entregue"], weights=[20, 30, 50]
        )[0]
    else:
        p_entregue = otif_alvo / 100
        p_falha = 1 - p_entregue
        return random.choices(
            ["entregue", "tentativa", "devolvido"],
            weights=[p_entregue, p_falha * 0.75, p_falha * 0.25],
        )[0]


def hora_aleatoria(inicio_h: int, fim_h: int) -> time:
    return time(random.randint(inicio_h, fim_h), random.choice([0, 15, 30, 45]))


class GeradorStress:
    def __init__(self, db, run_id: str):
        self.db = db
        self.run_id = run_id
        self.contador = 0
        # contadores para o resumo final
        self.total_remessas = 0
        self.total_ondas = 0
        self.total_eventos = 0
        self.total_alertas = 0
        self.erros = {"TIMEOUT_SAP": 0, "ATA_VENCIDA": 0, "ARQUIVO_CORROMPIDO": 0}
        self.otifs_amostrados: list[float] = []

    def _prox_id(self) -> int:
        self.contador += 1
        return self.contador

    # ── Infra base (carregada uma vez) ──────────────────────────────────────

    async def carregar_infra(self):
        res = await self.db.execute(select(CentroDistribuicao).where(CentroDistribuicao.ativo == True))
        self.cds = {cd.codigo: cd for cd in res.scalars().all()}

        res = await self.db.execute(select(Cliente))
        clientes = res.scalars().all()
        self.clientes = {
            "OSA": [c for c in clientes if c.codigo_sap],
            "ITJ": [c for c in clientes if c.codigo_ups],
        }
        if not self.clientes["OSA"] or not self.clientes["ITJ"]:
            raise RuntimeError(
                "Base de clientes incompleta — rode 'python scripts/seed_demo.py' "
                "primeiro para popular clientes OSA/ITJ."
            )

        res = await self.db.execute(select(Transportadora).where(Transportadora.ativo == True))
        transportadoras = res.scalars().all()
        self.transportadoras = {
            cd_codigo: [t for t in transportadoras if t.cd_id == cd.id]
            for cd_codigo, cd in self.cds.items()
        }

        res = await self.db.execute(select(Veiculo).where(Veiculo.ativo == True))
        veiculos = res.scalars().all()
        self.veiculos = {
            cd_codigo: [v for v in veiculos if v.cd_id == cd.id]
            for cd_codigo, cd in self.cds.items()
        }

    # ── Um lote de dias ──────────────────────────────────────────────────────

    async def processar_lote(self, dias: list[date], hoje: date):
        uploads_buf, uploads_keys = [], []
        for dia in dias:
            for cd_codigo in ("OSA", "ITJ"):
                uploads_keys.append((dia, cd_codigo))
                sistema = "SAP" if cd_codigo == "OSA" else "UPS_WMS"
                uploads_buf.append({
                    "cd_id":          self.cds[cd_codigo].id,
                    "usuario":        "timoteo" if cd_codigo == "OSA" else "carlos",
                    "arquivo_nome":   f"Backlog_{sistema}_{cd_codigo}_{dia:%d%m%Y}.xlsx",
                    "arquivo_path":   f"/stress/{cd_codigo}_{dia:%Y%m%d}.xlsx",
                    "formato":        "xlsx",
                    "status":         "concluido",
                    "criado_em":      datetime.combine(dia, hora_aleatoria(6, 8)),
                })
        upload_ids = await self._inserir_returning(Upload, uploads_buf)
        upload_id_map = dict(zip(uploads_keys, upload_ids))

        remessas_buf, remessas_keys = [], []
        remessas_meta = {}  # numero_remessa -> dict com metadados úteis pra depois
        for dia in dias:
            otif_alvo = self._otif_do_dia(dia)
            n_osa, n_itj = volume_do_dia(dia)
            for cd_codigo, n in (("OSA", n_osa), ("ITJ", n_itj)):
                cd = self.cds[cd_codigo]
                clientes_cd = self.clientes[cd_codigo]
                for _ in range(n):
                    cliente = random.choice(clientes_cd)
                    numero = self._numero_remessa(cd_codigo, dia)

                    dias_passados = (hoje - dia).days
                    p_sem_nf = 0.12 if dias_passados == 0 else (0.04 if dias_passados <= 2 else 0.01)
                    nf_emitida = random.random() > p_sem_nf
                    status = distribuicao_status(dia, hoje, otif_alvo) if nf_emitida else "novo"

                    is_ata = bool(cliente.contrato_ata) and random.random() < 0.25
                    prazo_empenho = (dia + timedelta(days=random.randint(5, 60))) if is_ata else None

                    volume_m3 = round(random.uniform(0.3, 4.5), 3)
                    peso_kg   = round(random.uniform(15, 220), 2)
                    valor_nf  = round(random.uniform(3000, 50000), 2)

                    remessas_buf.append({
                        "numero_remessa": numero,
                        "origem":         "sap" if cd_codigo == "OSA" else "ups",
                        "cd_id":          cd.id,
                        "cliente_id":     cliente.id,
                        "data_extracao":  dia,
                        "data_upload":    datetime.combine(dia, hora_aleatoria(6, 8)),
                        "upload_id":      upload_id_map[(dia, cd_codigo)],
                        "volume_m3":      volume_m3,
                        "peso_kg":        peso_kg,
                        "valor_nf":       valor_nf,
                        "qtd_volumes":    random.randint(1, 8),
                        "status":         status,
                        "tipo_entrega":   "fracionado",
                        "prioridade":     "urgente" if is_ata else "normal",
                        "is_ata":         is_ata,
                        "numero_empenho": f"EMP-{self._prox_id():06d}" if is_ata else None,
                        "prazo_empenho":  prazo_empenho,
                        "nf_emitida":     nf_emitida,
                        "numero_nf":      f"NF-{self._prox_id():06d}" if nf_emitida else None,
                        "janela_inicio":  cliente.janela_inicio if not cliente.janela_flexivel else None,
                        "janela_fim":     cliente.janela_fim if not cliente.janela_flexivel else None,
                        "janela_critica": random.random() < 0.05,
                        "criado_em":      datetime.combine(dia, hora_aleatoria(6, 9)),
                    })
                    remessas_keys.append(numero)
                    remessas_meta[numero] = {
                        "dia": dia, "cd": cd_codigo, "status": status,
                        "nf_emitida": nf_emitida, "cliente_id": cliente.id,
                        "is_ata": is_ata, "prazo_empenho": prazo_empenho,
                        "volume_m3": volume_m3, "peso_kg": peso_kg, "valor_nf": valor_nf,
                    }

        remessa_ids = await self._inserir_returning(Remessa, remessas_buf)
        remessa_id_map = dict(zip(remessas_keys, remessa_ids))
        self.total_remessas += len(remessas_buf)

        # ── Planos + Ondas (só remessas com NF emitida entram em onda) ──────
        planos_buf, planos_keys = [], []
        for dia in dias:
            for cd_codigo in ("OSA", "ITJ"):
                planos_keys.append((dia, cd_codigo))
                dias_passados = (hoje - dia).days
                planos_buf.append({
                    "cd_id":      self.cds[cd_codigo].id,
                    "data_plano": dia,
                    "ciclo":      1,
                    "status":     "rascunho" if dias_passados == 0 else "aprovado",
                    "criado_por": "timoteo" if cd_codigo == "OSA" else "carlos",
                    "criado_em":  datetime.combine(dia, hora_aleatoria(7, 9)),
                })
        plano_ids = await self._inserir_returning(PlanoDia, planos_buf)
        plano_id_map = dict(zip(planos_keys, plano_ids))

        ondas_buf, ondas_keys = [], []
        onda_remessas_buf = []
        programacoes_buf = []
        for dia in dias:
            dias_passados = (hoje - dia).days
            onda_status = "planejada" if dias_passados == 0 else "fechada"
            for cd_codigo in ("OSA", "ITJ"):
                despachaveis = [
                    num for num in remessas_meta
                    if remessas_meta[num]["dia"] == dia
                    and remessas_meta[num]["cd"] == cd_codigo
                    and remessas_meta[num]["nf_emitida"]
                ]
                if not despachaveis:
                    continue
                random.shuffle(despachaveis)
                k = min(random.randint(3, 5), max(1, len(despachaveis) // 3) or 1, len(despachaveis))
                grupos = [despachaveis[i::k] for i in range(k)]
                regioes = random.sample(REGIOES[cd_codigo], k=min(k, len(REGIOES[cd_codigo])))
                transps = self.transportadoras[cd_codigo]
                veics   = self.veiculos[cd_codigo]

                for idx, grupo in enumerate(grupos):
                    if not grupo:
                        continue
                    regiao = regioes[idx % len(regioes)]
                    transp = random.choice(transps)
                    veic   = random.choice(veics) if veics else None
                    vol_total = sum(remessas_meta[n]["volume_m3"] for n in grupo)
                    peso_total = sum(remessas_meta[n]["peso_kg"] for n in grupo)
                    valor_total = sum(remessas_meta[n]["valor_nf"] for n in grupo)
                    tipo = "ftl" if vol_total >= 15.0 else "fracionado"
                    ocupacao = round(random.uniform(60.0, 95.0), 1)
                    onda_nome = f"Onda {idx+1:02d} — {regiao} ({'FTL' if tipo=='ftl' else 'Fracionado'})"

                    onda_key = (dia, cd_codigo, idx)
                    ondas_keys.append(onda_key)
                    ondas_buf.append({
                        "plano_id":          plano_id_map[(dia, cd_codigo)],
                        "numero_onda":       idx + 1,
                        "nome":              onda_nome,
                        "regiao":            regiao,
                        "tipo":              tipo,
                        "veiculo_id":        veic.id if veic else None,
                        "transportadora_id": transp.id,
                        "volume_total_m3":   round(vol_total, 2),
                        "peso_total_kg":     round(peso_total, 2),
                        "valor_total_nf":    round(valor_total, 2),
                        "ocupacao_pct":      ocupacao,
                        "horario_coleta":    hora_aleatoria(13, 17),
                        "status":            onda_status,
                        "justificativa": (
                            f"{'FTL' if tipo=='ftl' else 'Fracionado'} via {transp.nome} — "
                            f"{len(grupo)} remessa(s), {round(vol_total,1)}m³ ({ocupacao}% ocupação)"
                        ),
                        "criado_em": datetime.combine(dia, hora_aleatoria(9, 11)),
                    })
                    # guarda pra depois do insert (precisa do onda_id)
                    for seq, num in enumerate(grupo, start=1):
                        onda_remessas_buf.append((onda_key, num, seq))

                    enviado_em = datetime.combine(dia, hora_aleatoria(13, 17))
                    confirmado = onda_status == "fechada"
                    programacoes_buf.append({
                        "_onda_key":          onda_key,
                        "transportadora_id":  transp.id,
                        "canal":              transp.integracao or "email",
                        "destinatario_email": transp.email_operacoes,
                        "assunto":            f"Programação BD {cd_codigo} — {onda_nome} — {dia:%d/%m/%Y}",
                        "corpo":              "(gerado pela infra de teste de stress)",
                        "enviado_em":         enviado_em,
                        "status_envio":       "confirmado" if confirmado else "enviado",
                        "confirmado_em":      enviado_em + timedelta(hours=random.uniform(1, 4)) if confirmado else None,
                        "protocolo":          f"PROT-{self._prox_id():06d}" if confirmado else None,
                        "criado_em":          enviado_em,
                    })

        onda_ids = await self._inserir_returning(Onda, ondas_buf)
        onda_id_map = dict(zip(ondas_keys, onda_ids))
        onda_transportadora_map = dict(zip(ondas_keys, (o["transportadora_id"] for o in ondas_buf)))
        self.total_ondas += len(ondas_buf)

        await self._inserir(OndaRemessa, [
            {"onda_id": onda_id_map[k], "remessa_id": remessa_id_map[num], "sequencia": seq}
            for k, num, seq in onda_remessas_buf
        ])

        for p in programacoes_buf:
            p["onda_id"] = onda_id_map[p.pop("_onda_key")]
        await self._inserir(ProgramacaoColeta, programacoes_buf)

        # ── Histórico de eventos ─────────────────────────────────────────────
        eventos_buf = []
        for (dia, cd_codigo), upload_id in upload_id_map.items():
            eventos_buf.append(self._evento(
                timestamp=datetime.combine(dia, hora_aleatoria(6, 8)),
                tipo_evento="upload_processado", origem="ingestor",
                ator_nome="Agente Ingestor", cd_id=self.cds[cd_codigo].id,
                descricao=f"Upload processado — CD {cd_codigo} — {dia:%d/%m/%Y}",
                resultado="sucesso",
                dados_extra={"upload_id": upload_id},
            ))

        for (dia, cd_codigo, idx), onda_id in onda_id_map.items():
            base = datetime.combine(dia, hora_aleatoria(9, 11))
            eventos_buf.append(self._evento(
                timestamp=base, tipo_evento="decisao_agente", origem="montador",
                ator_nome="Agente Montador", cd_id=self.cds[cd_codigo].id,
                descricao=f"Onda {idx+1} montada — {cd_codigo} — {dia:%d/%m/%Y}",
                resultado="sucesso", dados_extra={"onda_id": onda_id},
            ))
            eventos_buf.append(self._evento(
                timestamp=base + timedelta(hours=random.uniform(1, 3)),
                tipo_evento="decisao_agente", origem="comunicador",
                ator_nome="Agente Comunicador", cd_id=self.cds[cd_codigo].id,
                transportadora_id=onda_transportadora_map[(dia, cd_codigo, idx)],
                descricao=f"Programação enviada à transportadora — onda {idx+1} — {dia:%d/%m/%Y}",
                resultado="sucesso", dados_extra={"onda_id": onda_id},
            ))

        for numero, meta in remessas_meta.items():
            if not meta["nf_emitida"] or meta["status"] == "novo":
                continue
            base = datetime.combine(meta["dia"], hora_aleatoria(9, 11))
            for etapa in FSM_ATE.get(meta["status"], []):
                eventos_buf.append(self._evento(
                    timestamp=base + FSM_OFFSET[etapa],
                    tipo_evento="mudanca_status", origem="monitor",
                    ator_nome="Agente Monitor",
                    remessa_id=remessa_id_map[numero], cd_id=self.cds[meta["cd"]].id,
                    descricao=f"Remessa {numero} → {etapa}",
                    resultado="sucesso", dados_extra={"status": etapa},
                ))

        # erros injetados: 8% dos dias TIMEOUT_SAP/ARQUIVO_CORROMPIDO, 2% ATA_VENCIDA
        for dia in dias:
            if random.random() < 0.08:
                codigo = random.choice(ERROS_CODIGOS_TIMEOUT)
                cd_codigo = random.choice(["OSA", "ITJ"])
                self.erros[codigo] += 1
                eventos_buf.append(self._evento(
                    timestamp=datetime.combine(dia, hora_aleatoria(6, 9)),
                    tipo_evento="erro_sistema", origem="resolvedor",
                    ator_nome="Agente Resolvedor", cd_id=self.cds[cd_codigo].id,
                    descricao=f"Erro {codigo} durante ingestão — CD {cd_codigo} — {dia:%d/%m/%Y}",
                    resultado="escalado_humano", gravidade="critico",
                    dados_extra={"codigo_erro": codigo, "contexto": {"cd": cd_codigo, "data": dia.isoformat()}},
                ))
            if random.random() < 0.02:
                candidatas = [
                    n for n, m in remessas_meta.items()
                    if m["dia"] == dia and m["is_ata"] and m["prazo_empenho"]
                    and (m["prazo_empenho"] - dia).days <= 3
                ]
                if candidatas:
                    numero = random.choice(candidatas)
                    meta = remessas_meta[numero]
                    self.erros["ATA_VENCIDA"] += 1
                    eventos_buf.append(self._evento(
                        timestamp=datetime.combine(dia, hora_aleatoria(6, 9)),
                        tipo_evento="erro_sistema", origem="resolvedor",
                        ator_nome="Agente Resolvedor",
                        remessa_id=remessa_id_map[numero], cd_id=self.cds[meta["cd"]].id,
                        descricao=f"Erro ATA_VENCIDA — remessa {numero} com empenho vencendo",
                        resultado="escalado_humano", gravidade="critico",
                        dados_extra={"codigo_erro": "ATA_VENCIDA", "contexto": {"remessa": numero}},
                    ))

        await self._inserir(HistoricoEventos, eventos_buf)
        self.total_eventos += len(eventos_buf)

        # ── Alertas ──────────────────────────────────────────────────────────
        alertas_buf = []
        for numero, meta in remessas_meta.items():
            dias_passados = (hoje - meta["dia"]).days
            if meta["status"] in ("tentativa", "devolvido"):
                criado_em = datetime.combine(meta["dia"], hora_aleatoria(15, 18))
                resolvido = dias_passados > 3
                alertas_buf.append({
                    "tipo":         "falha_entrega",
                    "severidade":   "critica" if meta["status"] == "devolvido" else "alta",
                    "titulo":       f"Falha de entrega — {numero}",
                    "descricao":    f"Remessa {numero} com status {meta['status']}.",
                    "remessa_id":   remessa_id_map[numero],
                    "cliente_id":   meta["cliente_id"],
                    "cd_id":        self.cds[meta["cd"]].id,
                    "resolvido":    resolvido,
                    "resolvido_em": criado_em + timedelta(hours=random.uniform(2, 48)) if resolvido else None,
                    "criado_em":    criado_em,
                })
            elif meta["is_ata"] and meta["prazo_empenho"] and dias_passados <= 3:
                dias_restantes = (meta["prazo_empenho"] - hoje).days
                if dias_restantes <= 5:
                    alertas_buf.append({
                        "tipo":         "ata_critica",
                        "severidade":   "critica",
                        "titulo":       f"Empenho ATA crítico — {numero}",
                        "descricao":    f"Remessa {numero} com empenho vencendo em {dias_restantes} dia(s).",
                        "remessa_id":   remessa_id_map[numero],
                        "cliente_id":   meta["cliente_id"],
                        "cd_id":        self.cds[meta["cd"]].id,
                        "resolvido":    False,
                        "resolvido_em": None,
                        "criado_em":    datetime.combine(meta["dia"], hora_aleatoria(8, 10)),
                    })
        await self._inserir(Alerta, alertas_buf)
        self.total_alertas += len(alertas_buf)

        await self.db.commit()

    def _otif_do_dia(self, dia: date) -> float:
        otif = otif_mensal(dia.month, dia.year)
        self.otifs_amostrados.append(otif)
        return otif

    def _numero_remessa(self, cd_codigo: str, dia: date) -> str:
        letra = "O" if cd_codigo == "OSA" else "I"
        return f"SX{letra}{dia:%y%m%d}{self._prox_id():05d}{self.run_id}"

    def _evento(self, **kwargs) -> dict:
        base = {
            "ator_tipo":    "agente_ia",
            "resultado":    "sucesso",
            "gravidade":    None,
            "visibilidade": "interno",
            "remessa_id":        None,
            "transportadora_id": None,
            "cd_id":             None,
            "dados_extra":       None,
        }
        base.update(kwargs)
        return base

    @staticmethod
    def _agrupar_por_assinatura_nula(registros: list[dict]) -> list[list[tuple[int, dict]]]:
        """Agrupa linhas por 'assinatura' de quais colunas são None.

        Misturar None e valor na MESMA coluna dentro de um único INSERT
        multi-linha faz o driver (asyncpg, via Supabase Transaction Pooler)
        cair num modo catastroficamente lento — 500 linhas heterogêneas
        chegam a levar 300-400s contra ~1s em lotes homogêneos. Agrupar por
        assinatura de nulidade antes de cada insert resolve, ao custo de
        alguns poucos INSERTs a mais por lote (não linha a linha).
        """
        grupos: dict[tuple, list[tuple[int, dict]]] = {}
        for idx, row in enumerate(registros):
            assinatura = tuple(sorted(k for k, v in row.items() if v is None))
            grupos.setdefault(assinatura, []).append((idx, row))
        return list(grupos.values())

    async def _inserir_returning(self, model, registros: list[dict]) -> list[int]:
        if not registros:
            return []
        ids_por_posicao: dict[int, int] = {}
        for grupo in self._agrupar_por_assinatura_nula(registros):
            indices = [idx for idx, _ in grupo]
            linhas  = [row for _, row in grupo]
            res = await self.db.execute(insert(model).returning(model.id), linhas)
            for idx, row in zip(indices, res.fetchall()):
                ids_por_posicao[idx] = row[0]
        return [ids_por_posicao[i] for i in range(len(registros))]

    async def _inserir(self, model, registros: list[dict]) -> None:
        if not registros:
            return
        for grupo in self._agrupar_por_assinatura_nula(registros):
            linhas = [row for _, row in grupo]
            await self.db.execute(insert(model), linhas)


async def limpar_dados_operacionais(db):
    print("  Limpando dados operacionais (modo=limpo)...")
    for tabela in TABELAS_OPERACIONAIS:
        res = await db.execute(text(f"DELETE FROM {tabela}"))
        print(f"    - {tabela}: {res.rowcount} removido(s)")
    await db.commit()


async def main():
    parser = argparse.ArgumentParser(description="Gera dados de stress test para o PEO-BD")
    parser.add_argument("--periodo", choices=PERIODOS.keys(), required=True)
    parser.add_argument("--modo", choices=["limpo", "adicionar"], default="adicionar")
    args = parser.parse_args()

    n_dias = PERIODOS[args.periodo]
    hoje = date.today()
    lista_dias = dias_uteis(n_dias)
    run_id = f"{random.randint(0, 9999):04d}"

    engine_kwargs: dict = {"echo": False}
    if "postgresql" in settings.DATABASE_URL:
        engine_kwargs["connect_args"] = DB_CONNECT_ARGS
    engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    inicio = time_module.perf_counter()

    async with SessionLocal() as db:
        if args.modo == "limpo":
            await limpar_dados_operacionais(db)

        gerador = GeradorStress(db, run_id)
        await gerador.carregar_infra()

        print(f"  Gerando {len(lista_dias)} dia(s) útil(eis) — {lista_dias[0]:%d/%m/%Y} a {lista_dias[-1]:%d/%m/%Y}\n")

        for i in range(0, len(lista_dias), CHUNK_DIAS):
            lote = lista_dias[i:i + CHUNK_DIAS]
            await gerador.processar_lote(lote, hoje)
            print(f"    ... {min(i + CHUNK_DIAS, len(lista_dias))}/{len(lista_dias)} dias processados "
                  f"({gerador.total_remessas} remessas, {gerador.total_ondas} ondas, {gerador.total_eventos} eventos)")

    await engine.dispose()

    duracao = time_module.perf_counter() - inicio
    otif_medio = sum(gerador.otifs_amostrados) / len(gerador.otifs_amostrados) if gerador.otifs_amostrados else 0.0
    total_erros = sum(gerador.erros.values())

    print(f"\nPeríodo: {args.periodo} ({len(lista_dias)} dias úteis)")
    print(f"Remessas inseridas: {gerador.total_remessas}")
    print(f"Ondas criadas: {gerador.total_ondas}")
    print(f"Eventos no histórico: {gerador.total_eventos}")
    print(f"Alertas gerados: {gerador.total_alertas}")
    print(
        f"Erros simulados: {total_erros} "
        f"(TIMEOUT_SAP: {gerador.erros['TIMEOUT_SAP']}, "
        f"ATA_VENCIDA: {gerador.erros['ATA_VENCIDA']}, "
        f"ARQUIVO_CORROMPIDO: {gerador.erros['ARQUIVO_CORROMPIDO']})"
    )
    print(f"Tempo total: {duracao:.1f}s")
    print(f"OTIF médio do período: {otif_medio:.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
