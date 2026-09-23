"""Unit tests for synthetic monitoring safety and state handling."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.models.db import SyntheticCheck
from app.services.synthetic_monitor import SyntheticMonitor, SyntheticTargetError, validate_synthetic_url


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://example.com", "only support"),
        ("https://user:pass@example.com", "embedded credentials"),
        ("https://example.com/path#fragment", "fragments"),
        ("http://localhost:8000/health", "Private/local"),
        ("http://127.0.0.1:8000/health", "Private or reserved"),
        ("http://10.0.0.5/health", "Private or reserved"),
    ],
)
def test_validate_synthetic_url_rejects_unsafe_targets(url: str, message: str) -> None:
    with pytest.raises(SyntheticTargetError, match=message):
        validate_synthetic_url(url, allow_private_targets=False)


def test_validate_synthetic_url_allows_private_targets_when_explicitly_enabled() -> None:
    assert (
        validate_synthetic_url("http://127.0.0.1:8000/health", allow_private_targets=True)
        == "http://127.0.0.1:8000/health"
    )


def test_validate_synthetic_url_normalizes_surrounding_whitespace() -> None:
    assert validate_synthetic_url("  https://example.com/health  ") == "https://example.com/health"


@pytest.mark.asyncio
async def test_synthetic_monitor_reuses_persisted_failure_streak(monkeypatch) -> None:
    class FakeResponse:
        status_code = 503

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url):
            return FakeResponse()

    monkeypatch.setattr(
        "app.services.synthetic_monitor.httpx.AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    from app.db.session import SessionLocal
    from app.models.db import Service, SyntheticCheck
    from uuid import uuid4

    check_id = str(uuid4())
    manager = AsyncMock()
    monitor = SyntheticMonitor(manager)
    with SessionLocal() as db:
        check = SyntheticCheck(
            id=check_id,
            name=f"unit-check-{uuid4()}",
            url="https://example.com/health",
            method="GET",
            interval_seconds=30,
            timeout_seconds=5,
            expected_status=200,
            enabled=False,
        )
        db.add(check)
        db.commit()

    first = await monitor.run_once(check_id)
    assert first.success is False
    assert first.consecutive_failures == 1

    second = await monitor.run_once(check_id)
    assert second.success is False
    assert second.consecutive_failures == 2

    assert manager.process_synthetic_check_result.await_count == 2
