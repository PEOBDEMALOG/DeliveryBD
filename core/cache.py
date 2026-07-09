# peo_bd/core/cache.py
# Cache em memória simples (dict de módulo) — sem Redis. Serve só enquanto a
# instância do processo estiver quente: em cold start (novo processo Vercel)
# o dict começa vazio. Suficiente para demo/endpoints cujos dados mudam pouco.
import time

_cache: dict = {}


def get_cached(key: str, ttl_seconds: int = 60):
    if key in _cache:
        valor, ts = _cache[key]
        if time.time() - ts < ttl_seconds:
            return valor
    return None


def set_cached(key: str, valor):
    _cache[key] = (valor, time.time())
