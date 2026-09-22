"""Administrator user management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.core.security import hash_password
from app.db.session import get_db
from app.models.db import User
from app.services.audit import add_audit_log

router = APIRouter(prefix="/api/users", tags=["users"])

VALID_ROLES = {"admin", "operator", "viewer"}


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="viewer", min_length=1, max_length=32)


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, min_length=1, max_length=32)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return current_user


def validate_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Role must be one of: {', '.join(sorted(VALID_ROLES))}",
        )
    return normalized


@router.get("", response_model=list[UserResponse])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.email)))


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Email must not be blank",
        )
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists",
        )

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        role=validate_role(payload.role),
    )
    db.add(user)
    add_audit_log(
        db,
        request=request,
        actor_user_id=current_user.id,
        action="user.updated",
        resource_type="user",
        resource_id=user.id,
        details={"fields": list(values)},
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    values = payload.model_dump(exclude_unset=True)
    if "role" in values:
        values["role"] = validate_role(values["role"])
    if "password" in values:
        user.password_hash = hash_password(values.pop("password"))

    if user.id == current_user.id and "role" in values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role",
        )

    if user.id == current_user.id and values.get("is_active") is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account",
        )

    removing_admin_access = (
        user.role == "admin"
        and user.is_active
        and (values.get("is_active") is False or values.get("role") not in (None, "admin"))
    )
    if removing_admin_access:
        active_admins = db.scalar(
            select(func.count()).select_from(User).where(
                User.role == "admin",
                User.is_active.is_(True),
            )
        ) or 0
        if active_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one active administrator account must remain",
            )

    for key, value in values.items():
        setattr(user, key, value)

    db.commit()
    db.refresh(user)
    return user
