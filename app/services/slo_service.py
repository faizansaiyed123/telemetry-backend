"""SLO/SLI evaluation over persisted telemetry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.db import SLO, TelemetryRecord


class SLOService:
    """Evaluate service-level objectives with database-side aggregation."""

    def evaluate(self, db: Session, slo: SLO, *, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now(timezone.utc)
        window_start = current - timedelta(hours=slo.window_hours)

        metric_column = getattr(TelemetryRecord, slo.metric)

        if slo.operator == ">":
            is_good = metric_column > slo.threshold
        elif slo.operator == ">=":
            is_good = metric_column >= slo.threshold
        elif slo.operator == "<":
            is_good = metric_column < slo.threshold
        else:
            is_good = metric_column <= slo.threshold

        stmt = (
            select(
                func.count(TelemetryRecord.id),
                func.coalesce(func.sum(case((is_good, 1), else_=0)), 0),
            )
            .where(
                TelemetryRecord.host_id == slo.host_id,
                TelemetryRecord.timestamp >= window_start,
                TelemetryRecord.timestamp < current,
            )
        )
        total_samples, good_samples = db.execute(stmt).one()
        total = int(total_samples or 0)
        good = int(good_samples or 0)
        bad = max(0, total - good)

        sli_percent = 100.0 if total == 0 else (good / total) * 100.0
        target = float(slo.objective_percent)
        allowed_bad = total * max(0.0, (100.0 - target) / 100.0)
        if allowed_bad <= 0:
            remaining_budget = 100.0 if bad == 0 else 0.0
        else:
            remaining_budget = max(0.0, min(100.0, ((allowed_bad - bad) / allowed_bad) * 100.0))

        return {
            "slo_id": slo.id,
            "name": slo.name,
            "host_id": slo.host_id,
            "metric": slo.metric,
            "operator": slo.operator,
            "threshold": slo.threshold,
            "objective_percent": target,
            "window_hours": slo.window_hours,
            "window_start": window_start,
            "window_end": current,
            "total_samples": total,
            "good_samples": good,
            "bad_samples": bad,
            "sli_percent": round(sli_percent, 4),
            "error_budget_percent": round(100.0 - target, 4),
            "error_budget_remaining_percent": round(remaining_budget, 4),
            "compliant": sli_percent >= target,
        }


slo_service = SLOService()
