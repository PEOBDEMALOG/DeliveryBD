#!/usr/bin/env python3
"""
Setup de Produção — PEO-BD
Roda uma única vez após o primeiro deploy para inicializar o banco de
produção (Supabase/Postgres). Idempotente — pode ser executado mais de uma
vez sem duplicar dados (cada etapa verifica o que já existe antes de inserir).

Sequência:
  1. Cria as tabelas (create_all — não apaga nada existente)
  2. Seed de infraestrutura: CDs, transportadoras, veículos, tabela de
     preços e clientes base (mesma rotina do startup do servidor)
  3. Seed do catálogo de 14 tipos de erro
  4. Seed das regras erro → ação do Agente Resolvedor

NÃO roda seed_historico_demo.py nem simular_erros_demo.py — esses geram
histórico e ocorrências fictícias para demonstração e não devem existir
num banco de produção.

Uso:
    DATABASE_URL=postgresql+asyncpg://... python scripts/setup_producao.py
"""

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import settings
from core.models import Base


async def main():
    print("=" * 60)
    print("PEO-BD — Setup de Produção")
    print("=" * 60)

    if "sqlite" in settings.DATABASE_URL:
        print(
            "\nAVISO: DATABASE_URL aponta para SQLite.\n"
            "Configure a variável de ambiente DATABASE_URL com a connection\n"
            "string do Supabase (pooler, porta 6543) antes de rodar este\n"
            "script contra o banco de produção.\n"
        )
    else:
        destino = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else settings.DATABASE_URL
        print(f"Banco de destino: {destino}")

    from api.main import engine, _seed_dados_base

    print("\n[1/4] Criando tabelas (se necessário)...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("      OK")

    print("\n[2/4] Seed de infraestrutura (CDs, transportadoras, veículos, clientes base)...")
    await _seed_dados_base()
    print("      OK (idempotente — não duplica se já existir)")

    print("\n[3/4] Seed do catálogo de tipos de erro...")
    from scripts.seed_tipos_erro import seed as seed_tipos_erro
    await seed_tipos_erro()

    print("\n[4/4] Seed das regras erro → ação...")
    from scripts.seed_erro_acoes import seed as seed_erro_acoes
    await seed_erro_acoes()

    await engine.dispose()

    print("\n" + "=" * 60)
    print("Setup de produção concluído.")
    print("Não foram rodados seed_historico_demo.py nem simular_erros_demo.py")
    print("(geram dados fictícios de demonstração — não usar em produção).")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
