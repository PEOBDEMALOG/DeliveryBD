#!/usr/bin/env python3
"""
Índices de performance — PEO-BD (Etapa 1 da infra de teste de stress)
Cria os índices usados pelas consultas mais frequentes de remessas e
histórico de eventos. Idempotente (IF NOT EXISTS) — seguro rodar mais
de uma vez. Rodar ANTES de qualquer seed/carga de dados de stress.

Uso:
    python scripts/criar_indexes.py
"""

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import settings, DB_CONNECT_ARGS

INDEXES = [
    ("idx_remessas_data",             "CREATE INDEX IF NOT EXISTS idx_remessas_data ON remessas(data_extracao)"),
    ("idx_remessas_status",           "CREATE INDEX IF NOT EXISTS idx_remessas_status ON remessas(status)"),
    ("idx_remessas_cd_data",          "CREATE INDEX IF NOT EXISTS idx_remessas_cd_data ON remessas(cd_id, data_extracao)"),
    ("idx_historico_timestamp",       "CREATE INDEX IF NOT EXISTS idx_historico_timestamp ON historico_eventos(timestamp)"),
    ("idx_historico_tipo",            "CREATE INDEX IF NOT EXISTS idx_historico_tipo ON historico_eventos(tipo_evento)"),
    ("idx_historico_transportadora",  "CREATE INDEX IF NOT EXISTS idx_historico_transportadora ON historico_eventos(transportadora_id, timestamp)"),
]


async def criar_indexes():
    engine_kwargs: dict = {"echo": False}
    if "postgresql" in settings.DATABASE_URL:
        engine_kwargs["connect_args"] = DB_CONNECT_ARGS
    engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

    print(f"  Conectando em: {settings.DATABASE_URL.split('@')[-1] if '@' in settings.DATABASE_URL else settings.DATABASE_URL}\n")

    async with engine.begin() as conn:
        for nome, stmt in INDEXES:
            await conn.execute(text(stmt))
            print(f"  ✓ {nome}")

    await engine.dispose()
    print(f"\n✓ {len(INDEXES)} índice(s) garantido(s) (criado ou já existente).\n")


if __name__ == "__main__":
    asyncio.run(criar_indexes())
