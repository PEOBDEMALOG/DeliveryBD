# peo_bd/core/auth.py
# Autenticação JWT stateless — 3 usuários fictícios para demo.
# Senhas em texto puro propositalmente: são credenciais de demonstração,
# não há cadastro de usuários reais nem persistência em banco.

from datetime import datetime, timedelta

from jose import jwt

from core.config import settings

ALGORITHM = "HS256"

USUARIOS = {
    "timoteo": {
        "senha": "***REDACTED***",
        "nome":  "Timoteo Silva",
        "cd":    "OSA",
        "role":  "operador",
    },
    "carlos": {
        "senha": "***REDACTED***",
        "nome":  "Carlos Mendes",
        "cd":    "ITJ",
        "role":  "operador",
    },
    "erick": {
        "senha": "***REDACTED***",
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
