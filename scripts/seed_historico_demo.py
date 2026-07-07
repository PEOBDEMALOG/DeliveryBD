#!/usr/bin/env python3
"""
Seed do Histórico de Demonstração — PEO-BD
Gera eventos históricos realistas nos últimos 7 dias, distribuídos
entre as transportadoras cadastradas.

Não apaga dados existentes — apenas insere em historico_eventos.

Uso:
    python scripts/seed_historico_demo.py
"""

import asyncio
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select, func

from core.config import settings
from core.models import HistoricoEventos, Transportadora

random.seed(2025)

HOJE = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

# Taxa de erro (0–1) e volume de eventos por dia por transportadora.
# Taxas diferentes entre carriers para tornar a demo mais reveladora.
_PERFIL = {
    "DHL":      {"taxa_erro": 0.05, "vol_dia": 14},
    "JADLOG":   {"taxa_erro": 0.10, "vol_dia": 11},
    "UPS":      {"taxa_erro": 0.08, "vol_dia":  9},
    "TNT":      {"taxa_erro": 0.12, "vol_dia":  8},
    "FROTA_BD": {"taxa_erro": 0.03, "vol_dia":  5},
}
_PERFIL_PADRAO = {"taxa_erro": 0.08, "vol_dia": 6}

_REGIOES = ["capital_sp", "interior_sp", "sul", "interior_sp"]

_ARQUIVOS = [
    "backlog_SAP_20251223.xlsx",
    "remessas_SAP_20251224.csv",
    "backlog_SAP_20251225.xlsx",
    "upload_SAP_20251226.xlsx",
    "backlog_SAP_20251227.csv",
    "remessas_SAP_20251228.xlsx",
    "backlog_SAP_20251229.xlsx",
]

_MSGS_ERRO = [
    "Falha ao enviar e-mail para {nome} ({email}): Connection timeout após 30s",
    "Falha ao enviar e-mail para {nome} ({email}): SMTP AUTH failed — credencial recusada",
    "Falha ao enviar e-mail para {nome} ({email}): Mailbox temporariamente indisponível",
    "Falha ao enviar e-mail para {nome} ({email}): Connection refused — servidor SMTP fora do ar",
    "Falha ao enviar e-mail para {nome} ({email}): Recipient address rejected",
    "Erro ao processar linha {linha} do arquivo '{arq}': numero_remessa vazio",
    "Erro ao processar linha {linha} do arquivo '{arq}': volume_m3 inválido (valor negativo)",
    "Erro ao processar linha {linha} do arquivo '{arq}': prazo_empenho fora do formato esperado",
]


def _ts(dia_offset: int) -> datetime:
    """Timestamp aleatório num dia X dias atrás (entre 06h e 19h)."""
    base = HOJE - timedelta(days=dia_offset)
    return base.replace(
        hour=random.randint(6, 19),
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
    )


def _proto() -> str:
    return f"BD-{uuid.uuid4().hex[:8].upper()}"


def _gerar_para_transportadora(t: Transportadora, dia: int) -> list[dict]:
    perfil = _PERFIL.get(t.codigo, _PERFIL_PADRAO)
    n = random.randint(
        max(1, perfil["vol_dia"] - 2),
        perfil["vol_dia"] + 2,
    )
    eventos: list[dict] = []

    for _ in range(n):
        ts = _ts(dia)
        eh_erro = random.random() < perfil["taxa_erro"]

        if eh_erro:
            msg = random.choice(_MSGS_ERRO)
            descricao = msg.format(
                nome=t.nome,
                email=t.email_operacoes or "sem-email",
                linha=random.randint(2, 250),
                arq=random.choice(_ARQUIVOS),
            )
            eventos.append(dict(
                timestamp=ts,
                tipo_evento="erro_sistema",
                origem=random.choice(["comunicador", "ingestor"]),
                ator_tipo="agente_ia",
                ator_nome="Agente Comunicador",
                transportadora_id=t.id,
                descricao=descricao,
                resultado="falha",
                gravidade="alerta",
                visibilidade="interno",
                dados_extra=None,
            ))
            continue

        # Sucesso — distribui entre tipos de evento
        r = random.random()

        if r < 0.55:
            # decisao_agente: programação enviada (comunicador) — é o que conta para as stats
            num = random.randint(1, 8)
            proto = _proto()
            eventos.append(dict(
                timestamp=ts,
                tipo_evento="decisao_agente",
                origem="comunicador",
                ator_tipo="agente_ia",
                ator_nome="Agente Comunicador",
                transportadora_id=t.id,
                descricao=f"Programação enviada — Onda {num:02d} → {t.nome} | protocolo {proto}",
                resultado="sucesso",
                gravidade=None,
                visibilidade="interno",
                dados_extra={"protocolo": proto, "onda": num},
            ))

        elif r < 0.72:
            # decisao_agente: onda criada (montador)
            regiao = random.choice(_REGIOES)
            tipo_onda = random.choice(["FTL", "Fracionado"])
            qtd = random.randint(3, 14)
            vol = round(random.uniform(2.0, 27.0), 1)
            ocup = random.randint(58, 97)
            num = random.randint(1, 8)
            eventos.append(dict(
                timestamp=ts,
                tipo_evento="decisao_agente",
                origem="montador",
                ator_tipo="agente_ia",
                ator_nome="Agente Montador",
                transportadora_id=t.id,
                descricao=(
                    f"Onda {num:02d} criada — {regiao} ({tipo_onda}) — "
                    f"{qtd} remessas, {vol}m³, ocupação {ocup}%"
                ),
                resultado="sucesso",
                gravidade=None,
                visibilidade="interno",
                dados_extra={"numero_onda": num, "regiao": regiao, "volume_m3": vol, "ocupacao_pct": ocup},
            ))

        elif r < 0.85:
            # mudanca_status (monitor)
            pares = [
                ("planejado", "coletado"),
                ("coletado", "em_transito"),
                ("em_transito", "em_rota_entrega"),
                ("em_rota_entrega", "entregue"),
            ]
            s_ant, s_nov = random.choice(pares)
            rem = f"BK{random.randint(9000000, 9999999)}"
            eventos.append(dict(
                timestamp=ts,
                tipo_evento="mudanca_status",
                origem="monitor",
                ator_tipo="agente_ia",
                ator_nome="Agente Monitor",
                transportadora_id=t.id,
                descricao=(
                    f"Remessa {rem} — status atualizado: {s_ant} → {s_nov} "
                    f"[{t.codigo}, fonte: manual]"
                ),
                resultado="sucesso",
                gravidade="info" if s_nov == "entregue" else None,
                visibilidade="interno",
                dados_extra={"status_anterior": s_ant, "status_novo": s_nov},
            ))

        else:
            # upload_processado (ingestor) — sem transportadora específica
            arq = random.choice(_ARQUIVOS)
            validas = random.randint(22, 145)
            dup = random.randint(0, 6)
            orig = random.choice(["SAP", "UPS_WMS"])
            eventos.append(dict(
                timestamp=ts,
                tipo_evento="upload_processado",
                origem="ingestor",
                ator_tipo="agente_ia",
                ator_nome="Agente Ingestor",
                transportadora_id=None,
                descricao=(
                    f"Upload '{arq}' ({orig}) processado — "
                    f"{validas} válidas, {dup} duplicatas, 0 erros"
                ),
                resultado="sucesso",
                gravidade=None,
                visibilidade="interno",
                dados_extra={"validas": validas, "duplicatas": dup},
            ))

    return eventos


async def seed():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionLocal() as db:
        res = await db.execute(
            select(Transportadora)
            .where(Transportadora.ativo == True)
            .order_by(Transportadora.id)
        )
        transportadoras = list(res.scalars().all())

        if not transportadoras:
            print(
                "Nenhuma transportadora encontrada.\n"
                "Inicie o servidor pelo menos uma vez para criar os dados base."
            )
            await engine.dispose()
            return

        print(f"Gerando histórico para {len(transportadoras)} transportadora(s) × 7 dias...")

        contadores: dict[str, dict] = {}
        total = 0

        for dia in range(7):  # dia=0 → hoje, dia=6 → 6 dias atrás
            for t in transportadoras:
                evs = _gerar_para_transportadora(t, dia)
                for ev in evs:
                    db.add(HistoricoEventos(**ev))

                # Acumula contadores para relatório final
                if t.codigo not in contadores:
                    contadores[t.codigo] = {"total": 0, "erros": 0, "nome": t.nome}
                contadores[t.codigo]["total"] += len(evs)
                contadores[t.codigo]["erros"] += sum(1 for e in evs if e["resultado"] == "falha")
                total += len(evs)

            await db.flush()

        await db.commit()

    await engine.dispose()

    print(f"\n✓ {total} eventos inseridos em historico_eventos\n")
    print(f"{'Transportadora':<30} {'Total':>7} {'Erros':>7} {'Taxa':>7}")
    print("-" * 55)
    for codigo, c in sorted(contadores.items()):
        taxa = c["erros"] / c["total"] * 100 if c["total"] else 0
        print(f"{c['nome']:<30} {c['total']:>7} {c['erros']:>7} {taxa:>6.1f}%")
    print()
    print("Acesse a aba Transportadoras no painel para visualizar os dados.")


if __name__ == "__main__":
    asyncio.run(seed())
