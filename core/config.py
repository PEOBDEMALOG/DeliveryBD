# peo_bd/core/config.py
import os
import ssl
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Carrega .env em dev local (no Vercel as env vars já vêm injetadas pela plataforma —
# não há .env no filesystem read-only, então isto é um no-op silencioso lá).
if os.getenv("VERCEL") != "1":
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")

# No Vercel os arquivos ficam em /var/task/ (read-only).
# Checamos pelo path E pela env var para máxima compatibilidade.
IS_VERCEL = (
    os.getenv("VERCEL") == "1"
    or str(BASE_DIR).startswith("/var/task")
)

# Banco fora do OneDrive para evitar sync que reverte dados (apenas local/Windows).
_LOCALAPPDATA = Path(os.getenv("LOCALAPPDATA", "")) if (os.name == "nt" and not IS_VERCEL) else None
_DB_DIR = Path("/tmp") if IS_VERCEL else (_LOCALAPPDATA / "peo_bd" if _LOCALAPPDATA else BASE_DIR / "data" / "db")


# Parâmetros que psycopg2/libpq (ou o Supabase/Prisma) aceitam mas o asyncpg não:
# asyncpg usa ssl=<SSLContext|True> em vez de sslmode=require, e o "pgbouncer=true"
# que o Supabase anexa às URLs do pooler não é um kwarg válido de conexão.
_ASYNCPG_STRIP_PARAMS = frozenset({
    "sslmode", "channel_binding", "connect_timeout",
    "sslrootcert", "sslcert", "sslkey", "application_name",
    "pgbouncer", "supa",
})


def _resolve_db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        if IS_VERCEL:
            url = "sqlite+aiosqlite:////tmp/peo_bd.db"
        else:
            url = f"sqlite+aiosqlite:///{_DB_DIR / 'peo_bd.db'}"
    # Supabase / Vercel Postgres entrega postgres:// — asyncpg precisa de postgresql+asyncpg://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    # Limpa parâmetros psycopg2-style do query string — asyncpg os rejeita como
    # kwargs desconhecidos (TypeError). SSL é configurado via connect_args no engine.
    if "postgresql+asyncpg" in url:
        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        parsed = urlparse(url)
        qs = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
              if k not in _ASYNCPG_STRIP_PARAMS}
        # Supabase Transaction Pooler (pgbouncer) exige cache de prepared statements
        # desativado — connect_args do SQLAlchemy não tem efeito aqui, só a query
        # string da própria URL é respeitada pelo dialect asyncpg.
        if "supabase" in url and "prepared_statement_cache_size" not in qs:
            qs["prepared_statement_cache_size"] = "0"
        url = urlunparse(parsed._replace(query=urlencode(qs)))
    return url


class Settings:
    # ── Banco de dados ──────────────────────────────────────
    DATABASE_URL: str = _resolve_db_url()
    DB_DIR: Path = _DB_DIR

    # ── Caminhos ────────────────────────────────────────────
    # No Vercel o filesystem do projeto é read-only; /tmp é o único dir gravável (512 MB, efêmero).
    UPLOAD_DIR: Path = Path("/tmp/peo_uploads") if IS_VERCEL else BASE_DIR / "data" / "uploads"
    OUTPUT_DIR: Path = Path("/tmp/peo_outputs") if IS_VERCEL else BASE_DIR / "data" / "outputs"

    # ── Claude API (motor de IA dos agentes) ────────────────
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = "claude-sonnet-4-6"

    # ── E-mail (SMTP para disparo de programações) ───────────
    SMTP_HOST:     str = os.getenv("SMTP_HOST", "smtp.office365.com")
    SMTP_PORT:     int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER:     str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    EMAIL_FROM:    str = os.getenv("EMAIL_FROM", "expedicao@bd.com")

    # ── Alertas por e-mail (Resend — transacional) ───────────
    RESEND_API_KEY:       str = os.getenv("RESEND_API_KEY", "")
    EMAIL_ALERTAS_EMALOG: str = os.getenv("EMAIL_ALERTAS_EMALOG", "erick.antonio@emalog.com.br")

    # ── Visibilidade de eventos ──────────────────────────────
    # Trava: nenhum endpoint expõe eventos com visibilidade="interno" externamente.
    # Quando False, a flag apenas_publico em /api/historico permitirá filtragem por portal BD.
    VISIBILIDADE_PUBLICA_BLOQUEADA: bool = True

    # ── Vercel Cron (varredura periódica do Resolvedor) ──────
    CRON_SECRET: str = os.getenv("CRON_SECRET", "dev-secret")

    # ── Autenticação JWT ──────────────────────────────────────
    JWT_SECRET: str = os.getenv("JWT_SECRET", "peo-bd-demo-secret-2026")

    # ── Regras de negócio BD ────────────────────────────────
    DIAS_ESPERA_FTL:         int   = 5
    LIMIAR_FTL_M3:           float = 15.0
    ALERTA_ATA_DIAS:         int   = 5
    ALERTA_JANELA_CRITICA_H: int   = 2
    OCUPACAO_MIN_FTL_PCT:    float = 0.70
    META_OTIF_PCT:           float = 0.95

    # ── CDs ─────────────────────────────────────────────────
    CD_OSASCO_ID:  int = 1
    CD_ITAJAI_ID:  int = 2


settings = Settings()

# SSL obrigatório para Postgres (Supabase exige TLS). No Supabase (direto ou
# via pooler) o certificado é assinado por uma CA que o bundle padrão do
# asyncpg/Python não reconhece, então a verificação é desligada explicitamente
# (check_hostname=False + CERT_NONE) — sem isso a conexão falha com
# [SSL: CERTIFICATE_VERIFY_FAILED].
#
# Duas camadas contra prepared statements sob o Supabase Transaction Pooler
# (pgbouncer), que não suporta prepared statements persistindo entre
# transações/conexões físicas:
# - "?prepared_statement_cache_size=0" na própria DATABASE_URL (ver
#   _resolve_db_url) e "statement_cache_size": 0 aqui: desligam o cache de
#   prepared statements do asyncpg (evita reuso de um statement já
#   invalidado pelo pooler).
# - "prepared_statement_name_func": gera um nome único por statement (em vez
#   do esquema default do asyncpg, que reaproveita nomes previsíveis) —
#   elimina colisões de nome entre conexões físicas diferentes do pooler,
#   que é a causa raiz do DuplicatePreparedStatementError.
DB_CONNECT_ARGS: dict = {}
if "postgresql+asyncpg" in settings.DATABASE_URL:
    if "supabase" in settings.DATABASE_URL:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        DB_CONNECT_ARGS = {
            "ssl": ssl_ctx,
            "statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{id(object())}_{int(time.time() * 1000)}__",
        }
    else:
        DB_CONNECT_ARGS = {"ssl": True}
