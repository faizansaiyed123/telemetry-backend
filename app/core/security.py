"""Authentication and password security helpers."""

from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def create_access_token(subject: str, role: str) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "role": role, "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def create_websocket_token(subject: str, role: str) -> str:
    """Create a short-lived token intended only for the telemetry WebSocket."""
    settings = get_settings()
    from app.core.ws_tokens import ws_token_registry

    jti = ws_token_registry.new_jti()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ws_token_registry.ttl_seconds)
    payload = {
        "sub": subject,
        "role": role,
        "aud": "telemetry-ws",
        "jti": jti,
        "exp": expires_at,
    }
    ws_token_registry.register(jti, expires_at.timestamp())
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_websocket_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        audience="telemetry-ws",
    )
