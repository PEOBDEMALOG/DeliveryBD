# peo_bd/core/auth.py
# Autenticação JWT stateless — 3 usuários fictícios para demo.
# Não há cadastro de usuários reais nem persistência em banco. Senhas vêm
# de variáveis de ambiente (core/config.py) — nunca hardcoded aqui: um
# valor fixo neste arquivo é uma credencial real exposta em texto puro no
# código-fonte, como já aconteceu antes (ver docs/adr/ sobre a rotação).

from datetime import datetime, timedelta

from jose import jwt

from core.config import settings

ALGORITHM = "HS256"

USUARIOS = {
    "timoteo": {
        "senha": settings.AUTH_SENHA_TIMOTEO,
        "nome":  "Timoteo Silva",
        "cd":    "OSA",
        "role":  "operador",
    },
    "carlos": {
        "senha": settings.AUTH_SENHA_CARLOS,
        "nome":  "Carlos Mendes",
        "cd":    "ITJ",
        "role":  "operador",
    },
    "erick": {
        "senha": settings.AUTH_SENHA_ERICK,
        "nome":  "Erick Antônio",
        "cd":    None,
        "role":  "admin",
    },
}


def criar_token(username: str) -> str:
    user = USUARIOS[username]
    payload = {
        "sub":  username,
        "nome": user["nome"],
        "cd":   user["cd"],
        "role": user["role"],
        "exp":  datetime.utcnow() + timedelta(hours=8),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def verificar_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
