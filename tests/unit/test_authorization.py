import pytest
from fastapi import HTTPException

from app.api.dependencies import require_admin, require_operator
from app.models.db import User


def make_user(role: str) -> User:
    return User(
        id="user-1",
        email="user@example.com",
        password_hash="unused",
        role=role,
        is_active=True,
    )


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_operator_allows_admin_and_operator(role: str) -> None:
    user = make_user(role)
    assert require_operator(user) is user


def test_operator_rejects_viewer() -> None:
    with pytest.raises(HTTPException) as exc:
        require_operator(make_user("viewer"))
    assert exc.value.status_code == 403


def test_admin_rejects_operator() -> None:
    with pytest.raises(HTTPException) as exc:
        require_admin(make_user("operator"))
    assert exc.value.status_code == 403


def test_admin_allows_admin() -> None:
    user = make_user("admin")
    assert require_admin(user) is user
