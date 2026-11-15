"""Anonymous, disposable recruiter demo sessions."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import TokenResponse, UserResponse, get_current_user
from app.core.config import get_settings
from app.core.rate_limit import RateLimitExceeded, rate_limiter
from app.core.security import create_access_token, hash_password
from app.db.session import get_db
from app.models.db import User
from app.services.demo_telemetry import DemoTelemetryRegistry

router = APIRouter(prefix="/api/demo", tags=["demo"])


class DemoSessionEndResponse(BaseModel):
    status: str
    detail: str


def _client_ip(request: Request) -> str:
    """Resolve the peer address, trusting Railway X-Real-IP only when opted in.

    Railway overwrites X-Real-IP at its public HTTP edge. Private-network and
    TCP-proxy ingress bypass that sanitization, so the setting must remain
    false unless this service is reachable only through the public HTTP edge.
    """
    settings = get_settings()
    peer = request.client.host if request.client else "unknown"
    if not settings.trust_railway_x_real_ip:
        return peer

    forwarded = request.headers.get("x-real-ip", "").strip()
    if not forwarded:
        return peer
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return peer


def _hash_client_ip(client_ip: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        client_ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@router.post(
    "/session",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an isolated temporary telemetry workspace",
)
async def create_demo_session(
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    settings = get_settings()
    if not settings.demo_enabled:
        raise HTTPException(status_code=404, detail="Demo mode is unavailable")

    registry: DemoTelemetryRegistry = request.app.state.demo_telemetry_registry
    ip_hash = _hash_client_ip(_client_ip(request))

    async with registry.creation_guard():
        try:
            rate_limiter.check(
                f"demo-bootstrap:{ip_hash}",
                limit=settings.demo_bootstrap_requests_per_ip,
                window_seconds=3600,
            )
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many demo sessions have been requested from this network.",
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc

        now = datetime.now(timezone.utc)
        active_count = db.scalar(
            select(func.count(User.id)).where(
                User.is_demo.is_(True),
                User.demo_ip_hash == ip_hash,
                User.is_active.is_(True),
                User.demo_expires_at > now,
            )
        ) or 0
        if active_count >= settings.demo_max_active_sessions_per_ip:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="The maximum number of active demo sessions for this network has been reached.",
                headers={"Retry-After": "60"},
            )

        expires_at = now + timedelta(minutes=settings.demo_session_ttl_minutes)
        user = User(
            email=f"demo-{uuid4().hex}@demo.invalid",
            password_hash=hash_password(secrets.token_urlsafe(48)),
            role="viewer",
            is_active=True,
            is_demo=True,
            demo_expires_at=expires_at,
            demo_ip_hash=ip_hash,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        try:
            await registry.create(user_id=user.id, expires_at=expires_at)
        except Exception as exc:
            db.delete(user)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The demo runtime could not be started. Please try again.",
            ) from exc

    token = create_access_token(
        user.id,
        user.role,
        expires_minutes=settings.demo_session_ttl_minutes,
        demo_session=True,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(
            id=user.id,
            email=user.email,
            role=user.role,
            is_active=user.is_active,
            is_demo=True,
            demo_expires_at=expires_at.isoformat(),
        ),
    )


@router.post(
    "/session/end",
    response_model=DemoSessionEndResponse,
    summary="Revoke and remove the current disposable demo workspace",
)
async def end_demo_session(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DemoSessionEndResponse:
    if not current_user.is_demo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No demo session exists for this identity",
        )

    await request.app.state.demo_telemetry_registry.end(current_user.id)
    db.delete(current_user)
    db.commit()
    return DemoSessionEndResponse(
        status="ended",
        detail="Demo access was revoked and the temporary telemetry runtime was stopped.",
    )
