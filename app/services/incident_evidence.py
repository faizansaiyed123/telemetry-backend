"""Deterministic incident evidence and telemetry regression analysis."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db import AlertRecord, TelemetryRecord


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
    if not alert_ids:
        return []

    alert_metrics = set(
        db.scalars(
            select(AlertRecord.metric).where(AlertRecord.id.in_(alert_ids))
        )
    )
    selected = [(metric, METRIC_COLUMNS[metric]) for metric in sorted(alert_metrics) if metric in METRIC_COLUMNS]
    if not selected:
        return []

    baseline_start = first_seen_at - timedelta(minutes=15)
    impact_end = max(last_seen_at, first_seen_at) + timedelta(minutes=1)

    def aggregate(start: datetime, end: datetime) -> dict[str, tuple[float | None, int]]:
        columns = []
        for metric, column in selected:
            columns.append(func.avg(column).label(f"{metric}_avg"))
            columns.append(func.count(column).label(f"{metric}_count"))

        stmt = select(*columns).where(
            TelemetryRecord.timestamp >= start,
            TelemetryRecord.timestamp < end,
        )
        if host_id is not None:
            stmt = stmt.where(TelemetryRecord.host_id == host_id)

        row = db.execute(stmt).one()
        values = {}
        for index, (metric, _) in enumerate(selected):
            values[metric] = (
                float(row[index * 2]) if row[index * 2] is not None else None,
                int(row[index * 2 + 1] or 0),
            )
        return values

    baseline = aggregate(baseline_start, first_seen_at)
    impact = aggregate(first_seen_at, impact_end)

    findings: list[tuple[float, str]] = []
    for metric, _ in selected:
        before, baseline_count = baseline[metric]
        during, impact_count = impact[metric]

        if before is None or during is None or baseline_count == 0 or impact_count == 0:
            findings.append(
                (
                    0.0,
                    f"{metric}: insufficient telemetry samples for a before/incident comparison "
                    f"(baseline={baseline_count}, incident={impact_count}).",
                )
            )
            continue

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
