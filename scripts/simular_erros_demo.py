#!/usr/bin/env python3
"""
Simulação e Validação de Erros — PEO-BD  (Item 13 + Item 17)
Gera 2–3 ocorrências de cada um dos 14 tipos canônicos de erro e as
processa através do AgenteResolvedor para validar o fluxo completo:

  ignorar_log       → HistoricoEventos tipo=acao_resolvedor, gravidade=info  (SEM e-mail)
  retry_automatico  → tenta callback falho N vezes → escalado_humano/critico (COM e-mail)
  escalar_humano    → escalado_humano/critico diretamente                     (COM e-mail)

sleep_override_seconds=0 evita espera real nos retries durante a demo.

Pré-requisitos:
  python scripts/seed_tipos_erro.py
  python scripts/seed_erro_acoes.py

Uso:
  python scripts/simular_erros_demo.py
"""

import asyncio
import random
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from core.config import settings
from core.models import TipoErro, ErroAcao, Transportadora, CentroDistribuicao
from agents.agente_resolvedor import AgenteResolvedor

random.seed(42)

# Callback que simula falha — representa a operação original que não consegue se recuperar.
async def _callback_falho():
    raise RuntimeError("Callback simulado — falha intencional para demonstração")


async def simular():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionLocal() as db:
        # Carrega tipos e regras
        res_tipos = await db.execute(select(TipoErro).order_by(TipoErro.codigo))
        tipos = list(res_tipos.scalars().all())

        if not tipos:
            print("Nenhum TipoErro encontrado. Execute seed_tipos_erro.py primeiro.")
            await engine.dispose()
            return

        res_regras = await db.execute(select(ErroAcao).where(ErroAcao.ativo == True))
        regras = {r.tipo_erro_codigo: r for r in res_regras.scalars().all()}

        if not regras:
            print("Nenhuma ErroAcao encontrada. Execute seed_erro_acoes.py primeiro.")
            await engine.dispose()
            return

        res_transp = await db.execute(
            select(Transportadora).where(Transportadora.ativo == True).limit(3)
        )
        transportadoras = list(res_transp.scalars().all())
        transp_nome = transportadoras[0].nome if transportadoras else "N/A"

        res_cd = await db.execute(
            select(CentroDistribuicao).where(CentroDistribuicao.ativo == True).limit(1)
        )
        cds = list(res_cd.scalars().all())
        cd_nome = cds[0].nome if cds else "CD Desconhecido"

        resolvedor = AgenteResolvedor(db)

        # Estatísticas por tipo de ação
        stats: list[dict] = []

        print(f"\n{'='*70}")
        print(f"  Simulação de Erros — PEO-BD  |  {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC")
        print(f"{'='*70}\n")

        for tipo in tipos:
            qtd = random.randint(2, 3)
            regra = regras.get(tipo.codigo)
            acao_esperada = regra.acao if regra else "sem_regra"

            # Usa retry_callback apenas para erros retry_automatico
            callback = _callback_falho if acao_esperada == "retry_automatico" else None

            resultados_tipo: list[dict] = []
            for i in range(qtd):
                ctx = {
                    "arquivo": f"backlog_SAP_2025122{i}.xlsx",
                    "cd": cd_nome,
                    "transportadora": transp_nome,
                    "ocorrencia": i + 1,
                }
                res = await resolvedor.tratar_erro(
                    tipo_erro_codigo=tipo.codigo,
                    contexto=ctx,
                    retry_callback=callback,
                    sleep_override_seconds=0,  # sem espera real na demo
                )
                resultados_tipo.append(res)

            # Todos deveriam ter o mesmo status para o mesmo tipo
            status_final = resultados_tipo[0]["status"]
            escalado = resultados_tipo[0]["escalado"]

            stats.append({
                "codigo":   tipo.codigo,
                "gravidade": tipo.gravidade,
                "acao":     acao_esperada,
                "status":   status_final,
                "escalado": escalado,
                "qtd":      qtd,
            })

            icone = "🔕" if not escalado else "🚨"
            print(f"  {icone} {tipo.codigo:<30} → {status_final:<12} escalado={str(escalado):<5}  ({qtd}×)")

        await db.commit()

    await engine.dispose()

    # Relatório final
    print(f"\n{'='*70}")
    print("  Relatório de Validação")
    print(f"{'='*70}\n")
    print(f"  {'Código':<30} {'Ação':<25} {'Status':<14} {'E-mail?'}")
    print(f"  {'-'*75}")

    _ACAO_ICON = {
        "ignorar_log":      "🔕 ignorar_log     ",
        "retry_automatico": "🔄 retry_auto      ",
        "escalar_humano":   "🚨 escalar_humano  ",
        "sem_regra":        "❓ sem_regra        ",
    }
    total = ignorados = resolvidos = escalados = 0
    for s in stats:
        icone_acao = _ACAO_ICON.get(s["acao"], s["acao"])
        email_esperado = "✓ SIM" if s["escalado"] else "✗ não"
        print(f"  {s['codigo']:<30} {icone_acao} {s['status']:<14} {email_esperado}")
        total += s["qtd"]
        if not s["escalado"]:
            ignorados += s["qtd"]
        else:
            escalados += s["qtd"]

    print(f"\n  Total de eventos gerados : {total}")
    print(f"  Silenciados (sem e-mail) : {ignorados}")
    print(f"  Escalados   (com e-mail) : {escalados}")
    print()
    print("  Verificações esperadas:")
    print("  ✓ ignorar_log  → tipo=acao_resolvedor, gravidade=info,    SEM e-mail")
    print("  ✓ retry_auto   → tipo=erro_sistema,    gravidade=critico, COM e-mail (após N falhas)")
    print("  ✓ escalar_hum  → tipo=erro_sistema,    gravidade=critico, COM e-mail (imediato)")
    print()
    print("  Consulte a aba Painel de Diagnóstico ou /api/historico?tipo_evento=acao_resolvedor")
    print()


if __name__ == "__main__":
    asyncio.run(simular())
