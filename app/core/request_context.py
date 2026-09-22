"""Request-scoped correlation context.

The request ID is propagated through HTTP response headers and logging so an
operator can connect one API call with all related log entries.
"""

from __future__ import annotations

from contextvars import ContextVar, Token


_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> Token[str]:
    return _request_id.set(value)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)
