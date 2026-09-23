"""Scheduled synthetic monitoring runner."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from urllib.parse import urlparse
import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.alerts import Alert
from app.models.db import SyntheticCheck, SyntheticCheckRun
from app.services.platform_metrics import platform_metrics
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


class SyntheticTargetError(ValueError):
    """Raised when a configured synthetic URL is unsafe or invalid."""


def validate_synthetic_url(url: str, *, allow_private_targets: bool | None = None) -> str:
    """Validate and normalize an HTTP(S) target before it is stored or requested."""
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise SyntheticTargetError("Synthetic checks only support http:// and https:// targets")
    if not parsed.hostname:
        raise SyntheticTargetError("Synthetic check URL must include a hostname")
    if parsed.username or parsed.password:
        raise SyntheticTargetError("Synthetic check URLs must not contain embedded credentials")

    hostname = parsed.hostname.rstrip(".").lower()
    blocked_names = {"localhost", "localhost.localdomain"}
    allow_private = (
        get_settings().synthetic_allow_private_targets
        if allow_private_targets is None
        else allow_private_targets
    )
    if not allow_private and (hostname in blocked_names or hostname.endswith(".local")):
        raise SyntheticTargetError("Private/local synthetic targets are disabled")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not allow_private and (
        address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
    ):
        raise SyntheticTargetError("Private or reserved synthetic targets are disabled")

    if parsed.fragment:
        raise SyntheticTargetError("Synthetic check URLs must not contain fragments")

    return normalized


class SyntheticMonitor:
    """Run bounded, persisted HTTP checks and feed failures into incident management."""

    def __init__(self, manager) -> None:
        self._manager = manager
        self._tasks: dict[str, asyncio.Task] = {}
        self._checks: dict[str, SyntheticCheck] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._active_alerts: dict[str, Alert] = {}
        self._stopping = False
        self.failure_threshold = get_settings().synthetic_failure_threshold

    async def start(self) -> None:
        self._stopping = False
        self.sync()
        logger.info("Synthetic monitor started (%d checks)", len(self._checks))

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Synthetic monitor stopped")

    def sync(self) -> None:
        """Reconcile scheduler tasks with persisted enabled checks."""
        with SessionLocal() as db:
            checks = list(db.scalars(select(SyntheticCheck).order_by(SyntheticCheck.name)))

        current_ids = {check.id for check in checks if check.enabled}
        for check_id, task in list(self._tasks.items()):
            if check_id not in current_ids:
                task.cancel()
                self._tasks.pop(check_id, None)

        self._checks = {check.id: check for check in checks if check.enabled}
        platform_metrics.set_gauge("synthetic_active_checks", len(self._checks))
        for check in self._checks.values():
            task = self._tasks.get(check.id)
            if task is None or task.done():
                self._tasks[check.id] = asyncio.create_task(self._run_loop(check.id))

    async def run_once(self, check_id: str) -> SyntheticCheckRun:
        """Execute one check immediately and persist the result."""
        check = self._checks.get(check_id)
        if check is None:
            with SessionLocal() as db:
                check = db.get(SyntheticCheck, check_id)
        if check is None:
            raise KeyError(f"Synthetic check {check_id} not found")

        target = validate_synthetic_url(check.url)
        started = asyncio.get_running_loop().time()
        status_code: int | None = None
        error: str | None = None
        success = False

        try:
            async with httpx.AsyncClient(
                timeout=check.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = await client.request(check.method, target)
            status_code = response.status_code
            success = status_code == check.expected_status
            if not success:
                error = f"Expected HTTP {check.expected_status}, received {status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            error = str(exc)[:500]
        duration_ms = (asyncio.get_running_loop().time() - started) * 1000

        with SessionLocal() as db:
            row = db.get(SyntheticCheck, check.id)
            if row is None:
                raise KeyError(f"Synthetic check {check.id} not found")
            previous = self._consecutive_failures.get(check.id, 0)
            consecutive_failures = 0 if success else previous + 1
            run = SyntheticCheckRun(
                check_id=check.id,
                checked_at=utc_now(),
                duration_ms=duration_ms,
                status_code=status_code,
                success=success,
                error=error,
                consecutive_failures=consecutive_failures,
            )
            db.add(run)
            db.commit()
            db.refresh(run)

        self._consecutive_failures[check.id] = consecutive_failures
        platform_metrics.increment("synthetic_checks_total")
        if success:
            platform_metrics.increment("synthetic_check_success_total")
        else:
            platform_metrics.increment("synthetic_check_failure_total")

        await self._manager.process_synthetic_check_result(
            check_id=check.id,
            name=check.name,
            service_id=check.service_id,
            run=run,
            failure_threshold=self.failure_threshold,
            expected_status=check.expected_status,
        )
        return run

    async def _run_loop(self, check_id: str) -> None:
        """Run one check on a stable interval until disabled or shutdown."""
        while not self._stopping:
            check = self._checks.get(check_id)
            if check is None or not check.enabled:
                return
            try:
                await self.run_once(check_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Synthetic check failed unexpectedly: %s", check_id)
            try:
                await asyncio.sleep(check.interval_seconds)
            except asyncio.CancelledError:
                raise

    def latest_runs(self, limit: int = 100) -> dict[str, SyntheticCheckRun]:
        """Load the latest run for each enabled check with one bounded query."""
        limit = max(1, min(limit, 500))
        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(SyntheticCheckRun)
                    .join(SyntheticCheck, SyntheticCheck.id == SyntheticCheckRun.check_id)
                    .where(SyntheticCheck.enabled.is_(True))
                    .order_by(SyntheticCheckRun.checked_at.desc())
                    .limit(limit)
                )
            )
        latest: dict[str, SyntheticCheckRun] = {}
        for row in rows:
            latest.setdefault(row.check_id, row)
        return latest

    def active_alerts(self) -> dict[str, Alert]:
        return dict(self._active_alerts)
