#!/usr/bin/env python3
"""
Seed do mapeamento Erro → Ação — PEO-BD
Popula a tabela erro_acoes com as regras de resolução automática para
cada um dos 14 tipos canônicos de erro.

Uso:
    python scripts/seed_erro_acoes.py
"""

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from core.config import settings, DB_CONNECT_ARGS
from core.models import ErroAcao, TipoErro

# (tipo_erro_codigo, acao, max_tentativas, intervalo_retry_segundos)
MAPEAMENTO = [
    ("TIMEOUT_SAP",              "retry_automatico", 3, 30),
    ("TIMEOUT_UPS",              "retry_automatico", 3, 30),
    ("ARQUIVO_CORROMPIDO",       "escalar_humano",   1,  0),
    ("COLUNA_AUSENTE",           "escalar_humano",   1,  0),
    ("NF_DUPLICADA",             "ignorar_log",      1,  0),
    ("CLIENTE_NAO_RECONHECIDO",  "ignorar_log",      1,  0),
    ("REGIAO_INVALIDA",          "ignorar_log",      1,  0),
    ("FALHA_API_TRANSPORTADORA", "retry_automatico", 2, 60),
    ("CD_INDISPONIVEL",          "escalar_humano",   1,  0),
    ("VALOR_FORA_FAIXA",         "ignorar_log",      1,  0),
    ("ATA_VENCIDA",              "escalar_humano",   1,  0),
    ("HASH_DUPLICADO",           "ignorar_log",      1,  0),
    ("FALHA_PDF",                "retry_automatico", 2, 15),
    ("BANCO_INDISPONIVEL",       "escalar_humano",   1,  0),
]


async def seed():
    engine_kwargs = {"echo": False}
    if "postgresql" in settings.DATABASE_URL:
        engine_kwargs["connect_args"] = DB_CONNECT_ARGS
    engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionLocal() as db:
        # Verifica se tipos_erro existe
        res_tipos = await db.execute(select(TipoErro))
        codigos_existentes = {t.codigo for t in res_tipos.scalars().all()}

        inseridos = atualizados = ignorados = 0

        for codigo, acao, max_tent, intervalo in MAPEAMENTO:
            if codigo not in codigos_existentes:
                print(f"  AVISO: TipoErro '{codigo}' não existe — rode seed_tipos_erro.py primeiro.")
                ignorados += 1
                continue

            res = await db.execute(
                select(ErroAcao).where(ErroAcao.tipo_erro_codigo == codigo)
            )
            existente = res.scalar_one_or_none()

            if existente:
                existente.acao                     = acao
                existente.max_tentativas           = max_tent
                existente.intervalo_retry_segundos = intervalo
                existente.ativo                    = True
                atualizados += 1
            else:
                db.add(ErroAcao(
                    tipo_erro_codigo         = codigo,
                    acao                     = acao,
                    max_tentativas           = max_tent,
                    intervalo_retry_segundos = intervalo,
                    ativo                    = True,
                ))
                inseridos += 1

        await db.commit()

    await engine.dispose()

    print(f"\n✓ Mapeamento Erro → Ação sincronizado: {inseridos} inseridos, {atualizados} atualizados, {ignorados} ignorados\n")

    _ICON = {"retry_automatico": "🔄", "escalar_humano": "🚨", "ignorar_log": "🔕", "bloquear_remessa": "🚫"}
    print(f"{'Código':<30} {'Ação':<25} {'Tent.':>5} {'Intervalo':>10}")
    print("-" * 75)
    for codigo, acao, max_tent, intervalo in MAPEAMENTO:
        icone = _ICON.get(acao, " ")
        print(f"{codigo:<30} {icone} {acao:<23} {max_tent:>5} {intervalo:>8}s")
    print()


if __name__ == "__main__":
    asyncio.run(seed())
