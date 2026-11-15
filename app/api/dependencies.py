"""Shared API authentication, authorization and runtime dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.api.auth import get_current_user
from app.models.db import User
from app.services.telemetry_manager import TelemetryManager


def require_authenticated(current_user: User = Depends(get_current_user)) -> User:
    return current_user


def require_operator(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator access required",
        )
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return current_user


def require_operator_or_demo(current_user: User = Depends(get_current_user)) -> User:
    if current_user.is_demo:
        return current_user
    return require_operator(current_user)


def require_non_demo(current_user: User = Depends(get_current_user)) -> User:
    """Prevent disposable demo identities from reading shared database resources."""
    if current_user.is_demo:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This resource is not available in the isolated demo workspace",
        )
    return current_user


async def resolve_telemetry_manager(request: Request, current_user: User) -> TelemetryManager:
    """Resolve the runtime belonging to the authenticated identity."""
    if current_user.is_demo:
        registry = getattr(request.app.state, "demo_telemetry_registry", None)
        manager = await registry.get(current_user.id) if registry is not None else None
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Demo session is no longer active. Start a new demo session.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return manager
    return request.app.state.telemetry_manager


async def get_telemetry_manager(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> TelemetryManager:
    return await resolve_telemetry_manager(request, current_user)
