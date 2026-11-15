"""Shared API authentication and authorization dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.api.auth import get_current_user
from app.models.db import User


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
