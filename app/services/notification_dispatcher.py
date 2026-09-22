"""Durable, bounded webhook notification delivery."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import SessionLocal
from app.models.db import AlertRecord, Incident, NotificationChannel, NotificationDelivery
from app.models.observability import NOTIFICATION_EVENT_TYPES
from app.services.platform_metrics import platform_metrics

logger = logging.getLogger(__name__)

SEVERITY_RANK = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (1, 5, 30, 120)
POLL_INTERVAL_SECONDS = 1.0
MAX_DELIVERY_ERROR_LENGTH = 1000


@dataclass(frozen=True, slots=True)
class NotificationEnvelope:
    event_type: str
    event_id: str
    severity: str
    incident_id: str | None = None
    alert_id: str | None = None


def _safe_error(value: str | None) -> str:
    return (value or "unknown delivery error")[:MAX_DELIVERY_ERROR_LENGTH]


def validate_webhook_url(url: str) -> None:
    """Reject obvious SSRF targets and unsafe URL forms.

    Webhook destinations are administrator-controlled, but the server still
    validates them because an outbound HTTP client is an SSRF-sensitive sink.
    Only HTTPS URLs with public DNS/IP destinations are accepted.
    """

    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https":
        raise ValueError("Webhook URL must use HTTPS")
    if not parsed.hostname:
        raise ValueError("Webhook URL must contain a hostname")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Webhook URL must not contain credentials or fragments")
    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".internal")):
        raise ValueError("Private/local webhook destinations are not allowed")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if not literal_ip.is_global:
            raise ValueError("Webhook destination must be a public IP address")
        return

    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise ValueError("Webhook hostname could not be resolved") from exc

    if not addresses:
        raise ValueError("Webhook hostname could not be resolved")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("Webhook hostname resolved to an invalid address") from exc
        if not ip.is_global:
            raise ValueError("Webhook hostname resolves to a private or reserved address")


def _channel_accepts(channel: NotificationChannel, event_type: str, severity: str) -> bool:
    if not channel.enabled or channel.channel_type != "webhook":
        return False
    if event_type == "alert" and not channel.notify_alerts:
        return False
    if event_type == "incident" and not channel.notify_incidents:
        return False
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(channel.min_severity, 2)


class NotificationDispatcher:
    """Bounded asynchronous dispatcher with durable retry state.

    The telemetry runtime only places small notification envelopes onto the
    queue. All database work, network I/O, retries, and recovery from pending
    deliveries happen in this worker.
    """

    def __init__(
        self,
        *,
        queue_size: int = 500,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._queue: asyncio.Queue[NotificationEnvelope] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._timeout_seconds = timeout_seconds
        self.enqueued_events = 0
        self.dropped_events = 0
        self.deliveries_created = 0
        self.deliveries_succeeded = 0
        self.deliveries_failed = 0
        self.retry_count = 0

    async def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._worker())
            await self._recover_pending()

    async def stop(self) -> None:
        self._stopping = True
        if self._task is None:
            return
        task = self._task
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Notification worker did not drain before shutdown timeout; cancelling")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            self._task = None

    def enqueue_alert(self, alert: object) -> None:
        self._enqueue(
            NotificationEnvelope(
                event_type="alert",
                event_id=str(alert.id),
                severity=str(alert.severity),
                incident_id=getattr(alert, "incident_id", None),
                alert_id=str(alert.id),
            )
        )

    def enqueue_incident(self, incident: object) -> None:
        self._enqueue(
            NotificationEnvelope(
                event_type="incident",
                event_id=str(incident.id),
                severity=str(incident.severity),
                incident_id=str(incident.id),
            )
        )

    def _enqueue(self, envelope: NotificationEnvelope) -> None:
        try:
            self._queue.put_nowait(envelope)
            self.enqueued_events += 1
        except asyncio.QueueFull:
            self.dropped_events += 1
            platform_metrics.increment("notification_queue_dropped_total")
            logger.warning(
                "Notification queue full; dropping event type=%s id=%s",
                envelope.event_type,
                envelope.event_id,
            )

    async def _recover_pending(self) -> None:
        """Queue undelivered deliveries so application restarts resume work."""
        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.status == "pending",
                        or_(
                            NotificationDelivery.next_attempt_at.is_(None),
                            NotificationDelivery.next_attempt_at <= datetime.now(timezone.utc),
                        ),
                    )
                    .order_by(NotificationDelivery.created_at.asc())
                    .limit(self._queue.maxsize)
                )
            )
        for row in rows:
            try:
                self._queue.put_nowait(
                    NotificationEnvelope(
                        event_type=row.event_type,
                        event_id=row.event_id,
                        severity=row.severity,
                        incident_id=row.incident_id,
                        alert_id=row.alert_id,
                    )
                )
            except asyncio.QueueFull:
                break

    async def _worker(self) -> None:
        while True:
            if self._stopping and self._queue.empty():
                break

            try:
                envelope = await asyncio.wait_for(self._queue.get(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                await self._enqueue_due_deliveries()
                continue
            except asyncio.CancelledError:
                raise

            try:
                await self._materialize_and_dispatch(envelope)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Notification worker failed event_type=%s event_id=%s",
                    envelope.event_type,
                    envelope.event_id,
                )
                platform_metrics.increment("notification_worker_errors_total")

    async def _enqueue_due_deliveries(self) -> None:
        with SessionLocal() as db:
            rows = list(
                db.scalars(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.status == "pending",
                        or_(
                            NotificationDelivery.next_attempt_at.is_(None),
                            NotificationDelivery.next_attempt_at <= datetime.now(timezone.utc),
                        ),
                    )
                    .order_by(NotificationDelivery.created_at.asc())
                    .limit(50)
                )
            )
        for row in rows:
            try:
                self._queue.put_nowait(
                    NotificationEnvelope(
                        event_type=row.event_type,
                        event_id=row.event_id,
                        severity=row.severity,
                        incident_id=row.incident_id,
                        alert_id=row.alert_id,
                    )
                )
            except asyncio.QueueFull:
                break

    async def _materialize_and_dispatch(self, envelope: NotificationEnvelope) -> None:
        with SessionLocal() as db:
            channels = list(
                db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True)))
            )

            matching = [
                channel
                for channel in channels
                if _channel_accepts(channel, envelope.event_type, envelope.severity)
            ]
            for channel in matching:
                statement = (
                    pg_insert(NotificationDelivery)
                    .values(
                        channel_id=channel.id,
                        event_type=envelope.event_type,
                        event_id=envelope.event_id,
                        incident_id=envelope.incident_id,
                        alert_id=envelope.alert_id,
                        severity=envelope.severity,
                        status="pending",
                        attempts=0,
                    )
                    .on_conflict_do_nothing(constraint="uq_notification_delivery_event")
                    .returning(NotificationDelivery.id)
                )
                delivery_id = db.execute(statement).scalar_one_or_none()
                if delivery_id is not None:
                    self.deliveries_created += 1

            db.commit()

            delivery_rows = list(
                db.scalars(
                    select(NotificationDelivery).where(
                        NotificationDelivery.event_type == envelope.event_type,
                        NotificationDelivery.event_id == envelope.event_id,
                        NotificationDelivery.status == "pending",
                    )
                )
            )

        for row in delivery_rows:
            await self._deliver(row.id)

    async def _deliver(self, delivery_id: str) -> None:
        with SessionLocal() as db:
            delivery = db.get(NotificationDelivery, delivery_id)
            if delivery is None or delivery.status != "pending":
                return
            channel = db.get(NotificationChannel, delivery.channel_id)
            if channel is None or not channel.enabled:
                delivery.status = "failed"
                delivery.last_error = "Notification channel is unavailable"
                db.commit()
                self.deliveries_failed += 1
                return
            try:
                payload = self._build_payload(db, delivery)
            except Exception as exc:
                delivery.status = "failed"
                delivery.last_error = _safe_error(str(exc))
                db.commit()
                self.deliveries_failed += 1
                return

            delivery.attempts += 1
            delivery.last_attempt_at = datetime.now(timezone.utc)
            attempt_number = delivery.attempts
            webhook_url = channel.webhook_url
            db.commit()

        try:
            validate_webhook_url(webhook_url)
            async with httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=False) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Telemetry-Platform-Webhook/1.0",
                    },
                )
            if 200 <= response.status_code < 300:
                with SessionLocal() as db:
                    row = db.get(NotificationDelivery, delivery_id)
                    if row is not None:
                        row.status = "delivered"
                        row.delivered_at = datetime.now(timezone.utc)
                        row.next_attempt_at = None
                        row.last_error = None
                        db.commit()
                self.deliveries_succeeded += 1
                platform_metrics.increment("notification_delivered_total")
                return

            retryable = response.status_code == 429 or response.status_code >= 500
            error = f"Webhook returned HTTP {response.status_code}"
        except Exception as exc:
            retryable = True
            error = _safe_error(str(exc))

        with SessionLocal() as db:
            row = db.get(NotificationDelivery, delivery_id)
            if row is None or row.status != "pending":
                return
            if not retryable or attempt_number >= MAX_ATTEMPTS:
                row.status = "failed"
                row.last_error = error
                row.next_attempt_at = None
                db.commit()
                self.deliveries_failed += 1
                platform_metrics.increment("notification_failed_total")
                return

            delay = BACKOFF_SECONDS[min(attempt_number - 1, len(BACKOFF_SECONDS) - 1)]
            row.next_attempt_at = datetime.now(timezone.utc).replace(microsecond=0)
            row.next_attempt_at = row.next_attempt_at + __import__("datetime").timedelta(seconds=delay)
            row.last_error = error
            db.commit()

        self.retry_count += 1
        platform_metrics.increment("notification_retries_total")

    @staticmethod
    def _build_payload(db: Session, delivery: NotificationDelivery) -> dict[str, object]:
        if delivery.event_type not in NOTIFICATION_EVENT_TYPES:
            raise ValueError("Unsupported notification event type")

        if delivery.event_type == "alert":
            if not delivery.alert_id:
                raise ValueError("Alert delivery is missing alert_id")
            alert = db.get(AlertRecord, delivery.alert_id)
            if alert is None:
                raise ValueError("Referenced alert no longer exists")
            return {
                "version": 1,
                "event": "alert.firing",
                "delivered_at": datetime.now(timezone.utc).isoformat(),
                "alert": {
                    "id": alert.id,
                    "host_id": alert.host_id,
                    "metric": alert.metric,
                    "severity": alert.severity,
                    "message": alert.message,
                    "value": alert.value,
                    "timestamp": alert.timestamp.isoformat(),
                    "source": alert.source,
                    "rule_id": alert.rule_id,
                    "incident_id": delivery.incident_id,
                },
            }

        if not delivery.incident_id:
            raise ValueError("Incident delivery is missing incident_id")
        incident = db.get(Incident, delivery.incident_id)
        if incident is None:
            raise ValueError("Referenced incident no longer exists")
        return {
            "version": 1,
            "event": "incident.updated",
            "delivered_at": datetime.now(timezone.utc).isoformat(),
            "incident": {
                "id": incident.id,
                "host_id": incident.host_id,
                "title": incident.title,
                "severity": incident.severity,
                "status": incident.status,
                "first_seen_at": incident.first_seen_at.isoformat(),
                "last_seen_at": incident.last_seen_at.isoformat(),
                "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            },
        }

    def runtime_metrics(self) -> dict[str, int | float]:
        return {
            "queue_depth": self._queue.qsize(),
            "enqueued_events": self.enqueued_events,
            "dropped_events": self.dropped_events,
            "deliveries_created": self.deliveries_created,
            "deliveries_succeeded": self.deliveries_succeeded,
            "deliveries_failed": self.deliveries_failed,
            "retry_count": self.retry_count,
        }
