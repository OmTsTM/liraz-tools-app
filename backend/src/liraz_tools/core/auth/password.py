"""Hash de senha com bcrypt.

Bcrypt já incorpora salt e custo no próprio hash, então 1 string ~60
chars cobre tudo. `cost=12` é o sweet-spot atual (~250ms por hash em
máquinas modernas) — mais alto fica lento no login, mais baixo é
inseguro.
"""
from __future__ import annotations

import bcrypt

_COST = 12


def hash_password(plain: str) -> str:
    """Gera hash bcrypt da senha. Devolve string ASCII pronta pro banco."""
    if not plain:
        raise ValueError("senha vazia")
    hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=_COST))
    return hashed.decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """True sse a senha bate com o hash. Constant-time (bcrypt cuida disso)."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Hash corrompido / formato inválido → trata como falha de login.
        return False
