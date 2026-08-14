"""Cookie de sessão assinado via itsdangerous (HMAC-SHA256).

Conteúdo: `{"uid": "<user_uuid>"}` serializado JSON, assinado +
timestamp. Validação rejeita cookie expirado ou adulterado. NÃO é
criptografado — o conteúdo é legível por quem tem o cookie, mas
ninguém consegue forjar um sem o secret.
"""
from __future__ import annotations

from uuid import UUID

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from liraz_tools.core.config import get_settings


def _serializer() -> URLSafeTimedSerializer:
    """Não cacheia — se o secret mudar via env em runtime, o próximo
    request usa o novo. Custo de criar o serializer é desprezível."""
    s = get_settings()
    return URLSafeTimedSerializer(s.auth_session_secret, salt="liraz-session")


def emitir_token(user_id: UUID) -> str:
    """Gera o token pra escrever no cookie `liraz_session`."""
    return _serializer().dumps({"uid": str(user_id)})


def verificar_token(token: str) -> UUID | None:
    """Decodifica + valida assinatura + checa TTL.

    Devolve `user_id` se OK. None se: assinatura inválida, expirado,
    payload corrompido. Quem chama não precisa diferenciar — todas as
    falhas viram "não logado".
    """
    s = get_settings()
    max_age = s.auth_session_ttl_hours * 3600
    try:
        payload = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    uid = payload.get("uid")
    if not isinstance(uid, str):
        return None
    try:
        return UUID(uid)
    except ValueError:
        return None
