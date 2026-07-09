# peo_bd/core/db.py
# Engine/sessionmaker compartilhados — extraído de api/main.py para que outros
# módulos (ex.: agents/agente_monitor.py) possam abrir sessões concorrentes
# extras para paralelizar leituras independentes, sem import circular com a API.
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from core.config import settings, IS_VERCEL, DB_CONNECT_ARGS

_engine_kwargs: dict = {"echo": False}
if "postgresql" in settings.DATABASE_URL:
    _engine_kwargs["connect_args"] = DB_CONNECT_ARGS
    if IS_VERCEL:
        # NullPool: sem pool no lado da aplicação — correto para Vercel serverless,
        # que já não mantém estado entre requests (o Supabase Transaction Pooler faz
        # a multiplexação de conexões do lado dele).
        _engine_kwargs["poolclass"] = NullPool
    else:
        # Processo persistente (uvicorn local ou servidor tradicional): reaproveita
        # conexões entre requests em vez de reabrir handshake TCP/TLS/auth contra o
        # Postgres remoto a cada request — com NullPool isso custava ~1-2s extra por
        # request medidos contra este banco Supabase, em cima da latência de rede.
        _engine_kwargs["pool_size"]    = 5
        _engine_kwargs["max_overflow"] = 10
        _engine_kwargs["pool_pre_ping"] = True
        # Supabase (ou algum salto de rede no caminho) derruba conexões ociosas
        # silenciosamente — sem isso, pool_pre_ping só percebe a conexão morta ao
        # tentar usá-la, e o retry por trás de um socket morto pode travar ~20-40s
        # antes de desistir (medido neste ambiente). Reciclar proativamente evita
        # depender de pegar a conexão já morta.
        _engine_kwargs["pool_recycle"] = 180

engine            = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Supabase Transaction Pooler (pgbouncer): a prevenção real contra prepared
# statements duplicados/inválidos está toda em DB_CONNECT_ARGS (ssl,
# statement_cache_size=0, prepared_statement_name_func) e em
# "?prepared_statement_cache_size=0" na DATABASE_URL — ver core/config.py.
# Este listener usa só a API pública de eventos do SQLAlchemy (sem tocar em
# internals do dialect, que variam entre versões do _vendor do Vercel).
if "supabase" in settings.DATABASE_URL:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, connection_record):
        pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
