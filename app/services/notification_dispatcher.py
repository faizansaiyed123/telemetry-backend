"""Reliable, signed webhook notification delivery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.db import NotificationChannel, NotificationDelivery
from app.services.platform_metrics import platform_metrics
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}
_RETRYABLE_STATUS = {408, 425, 429}


@dataclass(frozen=True, slots=True)
class WebhookChannel:
    id: str
    name: str
    url: str
    event_types: frozenset[str]
    min_severity: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class NotificationJob:
    delivery_id: str
    channel_id: str
    url: str
    event_type: str
    event_id: str
    payload: dict


class NotificationDispatcher:
    """Bounded, restart-aware webhook delivery worker.

    Network I/O is never performed from the telemetry generation path. Alert
    transitions only enqueue jobs into a bounded in-memory queue. Each job is
    also persisted before delivery so pending work can be recovered after an
    application restart.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = settings.webhook_notifications_enabled
        self._signing_secret = settings.webhook_signing_secret
        self._timeout = settings.webhook_timeout_seconds
        self._max_attempts = settings.webhook_max_attempts
        self._queue: asyncio.Queue[NotificationJob] = asyncio.Queue(
            maxsize=settings.webhook_queue_size
        )
        self._channels: dict[str, WebhookChannel] = {}
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self._stopping = False
        self.dropped_jobs = 0

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        self._stopping = False
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=False,
        )
        self.reload()
        self._task = asyncio.create_task(self._worker())
        self._restore_pending_jobs()

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            task = self._task
            try:
                await asyncio.wait_for(task, timeout=15.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            finally:
                self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        platform_metrics.set_gauge("notification_queue_depth", 0)

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def reload(self) -> None:
        """Refresh enabled notification channels from the database."""
        if not self._enabled:
            self._channels = {}
            return
        try:
            with SessionLocal() as db:
                rows = list(
                    db.scalars(
                        select(NotificationChannel).order_by(NotificationChannel.name)
                    )
                )
            self._channels = {
                row.id: WebhookChannel(
                    id=row.id,
                    name=row.name,
                    url=row.url,
                    event_types=frozenset(
                        item.strip() for item in row.event_types.split(",") if item.strip()
                    ),
                    min_severity=row.min_severity,
                    enabled=row.enabled,
                )
                for row in rows
                if row.enabled
            }
        except Exception:
            logger.exception("Unable to reload webhook notification channels")

    def enqueue_alert(self, alert: object, action: str) -> None:
        """Queue an alert-created or alert-resolved notification."""
        event_type = f"alert.{action}"
        severity = getattr(getattr(alert, "severity", None), "value", getattr(alert, "severity", "INFO"))
        payload = {
            "version": 1,
            "event_type": event_type,
            "event_id": getattr(alert, "id"),
            "occurred_at": getattr(alert, "timestamp").isoformat(),
            "source": "telemetry-platform",
            "data": getattr(alert, "model_dump")(mode="json"),
        }
        self._enqueue(event_type, getattr(alert, "id"), payload, severity)

    def enqueue_incident(self, incident: object, action: str) -> None:
        """Queue an incident-created or incident-resolved notification."""
        event_type = f"incident.{action}"
        payload = {
            "version": 1,
            "event_type": event_type,
            "event_id": getattr(incident, "id"),
            "occurred_at": (getattr(incident, "resolved_at", None) or getattr(incident, "last_seen_at")).isoformat(),
            "source": "telemetry-platform",
            "data": {
                "id": getattr(incident, "id"),
                "host_id": getattr(incident, "host_id"),
                "title": getattr(incident, "title"),
                "status": getattr(incident, "status"),
                "severity": getattr(incident, "severity"),
                "first_seen_at": getattr(incident, "first_seen_at").isoformat(),
                "last_seen_at": getattr(incident, "last_seen_at").isoformat(),
            },
        }
        self._enqueue(event_type, getattr(incident, "id"), payload, getattr(incident, "severity"))

    def enqueue_test(self, channel_id: str) -> str:
        """Queue a connectivity test independent of configured event filters."""
        channel = self._channels.get(channel_id)
        if channel is None:
            raise KeyError(channel_id)
        delivery_id = uuid4().hex
        event_id = uuid4().hex
        payload = {
            "version": 1,
            "event_type": "notification.test",
            "event_id": event_id,
            "occurred_at": utc_now().isoformat(),
            "source": "telemetry-platform",
            "data": {"message": "Webhook connectivity test"},
        }
        job = NotificationJob(
            delivery_id=delivery_id,
            channel_id=channel.id,
            url=channel.url,
            event_type="notification.test",
            event_id=event_id,
            payload=payload,
        )
        if not self._queue_job(job):
            raise RuntimeError("Notification queue is full")
        return delivery_id

    def _enqueue(self, event_type: str, event_id: str, payload: dict, severity: str) -> None:
        if not self._enabled:
            return
        if not self._channels:
            return
        required_rank = _SEVERITY_RANK.get(severity, 1)
        for channel in tuple(self._channels.values()):
            if event_type not in channel.event_types:
                continue
            if required_rank < _SEVERITY_RANK.get(channel.min_severity, 2):
                continue
            job = NotificationJob(
                delivery_id=uuid4().hex,
                channel_id=channel.id,
                url=channel.url,
                event_type=event_type,
                event_id=event_id,
                payload=payload,
            )
            self._queue_job(job)

    def _queue_job(self, job: NotificationJob) -> bool:
        try:
            self._queue.put_nowait(job)
            platform_metrics.increment("notification_enqueued_total")
            platform_metrics.set_gauge("notification_queue_depth", self.queue_depth)
            return True
        except asyncio.QueueFull:
            self.dropped_jobs += 1
            platform_metrics.increment("notification_dropped_total")
            logger.warning(
                "Notification queue full; dropping delivery id=%s event=%s",
                delivery_id,
                job.event_type,
            )
            return False

    def _restore_pending_jobs(self) -> None:
        """Requeue pending/in-flight deliveries after restart."""
        if not self._enabled:
            return
        try:
            with SessionLocal() as db:
                rows = list(
                    db.scalars(
                        select(NotificationDelivery)
                        .where(NotificationDelivery.status.in_(("pending", "delivering")))
                        .order_by(NotificationDelivery.created_at.asc())
                        .limit(500)
                    )
                )
            for row in rows:
                channel = self._channels.get(row.channel_id)
                if channel is None:
                    continue
                try:
                    payload = json.loads(row.payload)
                except json.JSONDecodeError:
                    logger.error("Skipping malformed persisted webhook payload id=%s", row.id)
                    continue
                self._queue_job(
                    NotificationJob(
                        delivery_id=row.id,
                        channel_id=row.channel_id,
                        url=row.target_url,
                        event_type=row.event_type,
                        event_id=row.event_id,
                        payload=payload,
                    )
                )
        except Exception:
            logger.exception("Unable to restore pending webhook notifications")

    def retry_delivery(self, delivery_id: str) -> bool:
        """Requeue a failed persisted delivery using its original immutable payload."""
        if not self._enabled:
            return False

        with SessionLocal() as db:
            row = db.get(NotificationDelivery, delivery_id)
            if row is None or row.status != "failed":
                return False
            channel = self._channels.get(row.channel_id)
            if channel is None:
                return False
            try:
                payload = json.loads(row.payload)
            except json.JSONDecodeError:
                return False

            row.status = "pending"
            row.last_error = None
            row.last_status_code = None
            db.commit()

        queued = self._queue_job(
            NotificationJob(
                delivery_id=row.id,
                channel_id=row.channel_id,
                url=row.target_url,
                event_type=row.event_type,
                event_id=row.event_id,
                payload=payload,
            )
        )
        if not queued:
            with SessionLocal() as db:
                current = db.get(NotificationDelivery, delivery_id)
                if current is not None:
                    current.status = "failed"
                    current.last_error = "Notification queue is full"
                    db.commit()
            return False
        return True

    async def _worker(self) -> None:
        while True:
            if self._stopping and self._queue.empty():
                return
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                await self._deliver(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected webhook delivery worker error for %s", job.delivery_id)
            finally:
                self._queue.task_done()
                platform_metrics.set_gauge("notification_queue_depth", self.queue_depth)

    async def _deliver(self, job: NotificationJob) -> None:
        if self._client is None:
            return
        initial_body = json.dumps(job.payload, separators=(",", ":"), sort_keys=True)
        payload_hash = hashlib.sha256(initial_body.encode("utf-8")).hexdigest()
        delivery_id = self._ensure_delivery_row(job, initial_body, payload_hash)
        if delivery_id is None:
            logger.error("Unable to establish durable webhook delivery id=%s", job.delivery_id)
            return

        with SessionLocal() as db:
            stored = db.get(NotificationDelivery, delivery_id)
            if stored is None:
                logger.error("Durable webhook delivery disappeared id=%s", delivery_id)
                return
            if stored.status == "delivered":
                return
            body = stored.payload
            target_url = stored.target_url
            event_type = stored.event_type
            previous_attempts = stored.attempts

        last_error: str | None = None
        last_status: int | None = None
        delivery_timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        for attempt in range(1, self._max_attempts + 1):
            total_attempts = previous_attempts + attempt
            self._set_delivery_state(
                delivery_id,
                status="delivering",
                attempts=total_attempts,
                last_status_code=last_status,
                last_error=last_error,
            )
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "TelemetryPlatform-Webhook/1.0",
                "X-Telemetry-Event": event_type,
                "X-Telemetry-Delivery": delivery_id,
            }
            headers["X-Telemetry-Timestamp"] = delivery_timestamp
            if self._signing_secret:
                signed_message = f"{delivery_timestamp}.{body}".encode("utf-8")
                signature = hmac.new(
                    self._signing_secret.encode("utf-8"),
                    signed_message,
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Telemetry-Signature"] = f"sha256={signature}"

            try:
                response = await self._client.post(target_url, content=body, headers=headers)
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    self._set_delivery_state(
                        delivery_id,
                        status="delivered",
                        attempts=total_attempts,
                        last_status_code=response.status_code,
                        last_error=None,
                        delivered_at=utc_now(),
                    )
                    platform_metrics.increment("notification_delivered_total")
                    return
                last_error = f"Webhook returned HTTP {response.status_code}"
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            should_retry = (
                last_status in _RETRYABLE_STATUS
                or (last_status is not None and last_status >= 500)
                or last_status is None
            )
            if not should_retry or attempt >= self._max_attempts:
                self._set_delivery_state(
                    delivery_id,
                    status="failed",
                    attempts=total_attempts,
                    last_status_code=last_status,
                    last_error=last_error,
                )
                platform_metrics.increment("notification_failed_total")
                logger.warning(
                    "Webhook delivery failed id=%s event=%s attempts=%d status=%s",
                    delivery_id,
                    event_type,
                    total_attempts,
                    last_status,
                )
                return

            platform_metrics.increment("notification_retry_total")
            await asyncio.sleep(min(2 ** (attempt - 1), 4))

    def _ensure_delivery_row(self, job: NotificationJob, body: str, payload_hash: str) -> str | None:
        with SessionLocal() as db:
            by_id = db.get(NotificationDelivery, job.delivery_id)
            if by_id is not None:
                return by_id.id

            existing = db.scalar(
                select(NotificationDelivery).where(
                    NotificationDelivery.channel_id == job.channel_id,
                    NotificationDelivery.event_type == job.event_type,
                    NotificationDelivery.event_id == job.event_id,
                )
            )
            if existing is not None:
                return existing.id

            try:
                db.add(
                    NotificationDelivery(
                        id=job.delivery_id,
                        channel_id=job.channel_id,
                        event_type=job.event_type,
                        event_id=job.event_id,
                        status="pending",
                        attempts=0,
                        payload_sha256=payload_hash,
                        payload=body,
                        target_url=job.url,
                    )
                )
                db.commit()
                return job.delivery_id
            except IntegrityError:
                db.rollback()
                existing = db.scalar(
                    select(NotificationDelivery).where(
                        NotificationDelivery.channel_id == job.channel_id,
                        NotificationDelivery.event_type == job.event_type,
                        NotificationDelivery.event_id == job.event_id,
                    )
                )
                return existing.id if existing is not None else None

    def _set_delivery_state(
        self,
        delivery_id: str,
        *,
        status: str,
        attempts: int,
        last_status_code: int | None,
        last_error: str | None,
        delivered_at: datetime | None = None,
    ) -> None:
        with SessionLocal() as db:
            values = {
                "status": status,
                "attempts": attempts,
                "last_status_code": last_status_code,
                "last_error": last_error,
            }
            if delivered_at is not None:
                values["delivered_at"] = delivered_at
            db.execute(
                update(NotificationDelivery)
                .where(NotificationDelivery.id == delivery_id)
                .values(**values)
            )
            db.commit()

    @staticmethod
    def validate_url(url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Webhook URL must be an absolute http(s) URL")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Webhook URL must not contain credentials or a fragment")

        settings = get_settings()
        if settings.app_env.strip().lower() == "production" and parsed.scheme != "https":
            raise ValueError("Webhook URLs must use HTTPS in production")

        hostname = parsed.hostname.lower().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain", "metadata.google.internal"} or hostname.endswith(".localhost") or hostname.endswith(".local"):
            if settings.app_env.strip().lower() == "production":
                raise ValueError("Local or metadata webhook destinations are not allowed in production")

        if parsed.hostname:
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                return
            if address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified or address.is_multicast:
                if settings.app_env.strip().lower() == "production":
                    raise ValueError("Private or loopback webhook destinations are not allowed in production")


notification_dispatcher = NotificationDispatcher()
