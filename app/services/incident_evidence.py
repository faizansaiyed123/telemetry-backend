"""Deterministic incident evidence and telemetry regression analysis."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db import AlertRecord, IncidentAlert, TelemetryRecord


METRIC_COLUMNS = {
    "cpu": TelemetryRecord.cpu,
    "memory": TelemetryRecord.memory,
    "temperature": TelemetryRecord.temperature,
    "network_mbps": TelemetryRecord.network_mbps,
    "requests_per_second": TelemetryRecord.requests_per_second,
    "error_rate": TelemetryRecord.error_rate,
    "latency_ms": TelemetryRecord.latency_ms,
}


def build_metric_findings(
    db: Session,
    *,
    alert_ids: set[str],
    host_id: str | None,
    first_seen_at: datetime,
    last_seen_at: datetime,
) -> list[str]:
    """Compare pre-incident and incident-window averages without claiming causality."""
    alert_metrics = set(
        db.scalars(
            select(AlertRecord.metric).where(AlertRecord.id.in_(alert_ids))
        )
    ) if alert_ids else set()

    findings: list[tuple[float, str]] = []
    baseline_start = first_seen_at - timedelta(minutes=15)
    impact_end = max(last_seen_at, first_seen_at) + timedelta(minutes=1)

    for metric in sorted(alert_metrics):
        column = METRIC_COLUMNS.get(metric)
        if column is None:
            continue

        stmt = select(
            func.avg(column).label("avg"),
            func.count(TelemetryRecord.id).label("samples"),
        )

        baseline_stmt = stmt.where(
            TelemetryRecord.timestamp >= baseline_start,
            TelemetryRecord.timestamp < first_seen_at,
        )
        impact_stmt = stmt.where(
            TelemetryRecord.timestamp >= first_seen_at,
            TelemetryRecord.timestamp < impact_end,
        )

        if host_id is not None:
            baseline_stmt = baseline_stmt.where(TelemetryRecord.host_id == host_id)
            impact_stmt = impact_stmt.where(TelemetryRecord.host_id == host_id)

        baseline_avg, baseline_samples = db.execute(baseline_stmt).one()
        impact_avg, impact_samples = db.execute(impact_stmt).one()

        baseline_count = int(baseline_samples or 0)
        impact_count = int(impact_samples or 0)

        if baseline_count == 0 or impact_count == 0:
            findings.append(
                (
                    0.0,
                    f"{metric}: insufficient telemetry samples for a before/incident comparison "
                    f"(baseline={baseline_count}, incident={impact_count}).",
                )
            )
            continue

        before = float(baseline_avg)
        during = float(impact_avg)
        if before == 0:
            findings.append(
                (
                    0.0,
                    f"{metric}: incident-window average was {during:.2f}; baseline average was zero, "
                    "so percentage change is not meaningful.",
                )
            )
            continue

        pct_change = ((during - before) / abs(before)) * 100.0
        findings.append(
            (
                abs(pct_change),
                f"{metric}: average changed from {before:.2f} before the incident to "
                f"{during:.2f} during the incident ({pct_change:+.1f}%).",
            )
        )

    findings.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in findings[:5]]
