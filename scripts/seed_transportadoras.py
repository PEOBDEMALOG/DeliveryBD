#!/usr/bin/env python3
"""
Seed de meta_otif por transportadora — PEO-BD
Ajusta a meta de OTIF (%) de cada transportadora já cadastrada para um valor
realista de contrato. Idempotente — só atualiza, não cria transportadoras.

Uso:
    python scripts/seed_transportadoras.py
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
from core.models import Transportadora

# nome -> meta_otif (%). Contrato premium/frota própria toleram menos desvio
# que operação fracionada.
METAS = {
    "DHL Express Brasil":       97.0,  # contrato premium, SLA mais rígido
    "UPS Brasil":               96.0,  # internacional, SLA rígido
    "Frota Própria BD SP":      98.0,  # controle total da operação
    "Jadlog Logística":         94.0,  # fracionado, margem maior tolerada
    "TNT Mercúrio (FedEx)":     95.0,  # padrão — meta geral da BD
}


async def seed():
    engine_kwargs: dict = {"echo": False}
    if "postgresql" in settings.DATABASE_URL:
        engine_kwargs["connect_args"] = DB_CONNECT_ARGS
    engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async with SessionLocal() as db:
        res = await db.execute(select(Transportadora))
        transportadoras = res.scalars().all()

        atualizados = ignorados = 0
        for t in transportadoras:
            if t.nome not in METAS:
                print(f"  AVISO: sem meta definida para '{t.nome}' — mantido em {t.meta_otif}%.")
                ignorados += 1
                continue
            t.meta_otif = METAS[t.nome]
            atualizados += 1

        await db.commit()

    await engine.dispose()

    print(f"\n✓ meta_otif sincronizada: {atualizados} atualizada(s), {ignorados} ignorada(s)\n")
    print(f"{'Transportadora':<28} {'Meta OTIF':>10}")
    print("-" * 40)
    for nome, meta in METAS.items():
        print(f"{nome:<28} {meta:>9.1f}%")
    print()


if __name__ == "__main__":
    asyncio.run(seed())
