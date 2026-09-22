"""Stateful threshold alert rule evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from app.models.alerts import Alert
from app.models.observability import ALERT_RULE_METRICS


@dataclass(slots=True)
class RuleState:
    condition_since: datetime | None = None
    last_fired_at: datetime | None = None
    active_alert_id: str | None = None


@dataclass(slots=True)
class RuleTransition:
    alert: Alert
    action: str


def _matches(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    return False


class AlertRuleEngine:
    """Evaluate configurable rules without database work on the hot path."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._rules: dict[str, dict[str, object]] = {}
        self._states: dict[tuple[str, str], RuleState] = {}
        self.evaluations = 0
        self.fires = 0
        self.resolutions = 0

    def load_rules(self, rules: list[object]) -> None:
        with self._lock:
            self._rules = {
                str(rule.id): {
                    "id": str(rule.id),
                    "name": rule.name,
                    "metric": rule.metric,
                    "operator": rule.operator,
                    "threshold": float(rule.threshold),
                    "duration_seconds": float(rule.duration_seconds),
                    "cooldown_seconds": float(rule.cooldown_seconds),
                    "severity": str(rule.severity),
                    "enabled": bool(rule.enabled),
                }
                for rule in rules
                if rule.metric in ALERT_RULE_METRICS
            }
            self._states = {
                key: state
                for key, state in self._states.items()
                if key[0] in self._rules
            }

    def hydrate_active_alerts(self, alerts: list[Alert]) -> None:
        """Restore active rule alert state after an application restart."""
        with self._lock:
            for alert in alerts:
                if alert.source != "rule" or not alert.rule_id:
                    continue
                host_key = str(alert.host_id or "system")
                self._states[(alert.rule_id, host_key)] = RuleState(
                    condition_since=alert.timestamp,
                    last_fired_at=alert.timestamp,
                    active_alert_id=alert.id,
                )

    def upsert_rule(self, rule: object) -> None:
        with self._lock:
            self._rules[str(rule.id)] = {
                "id": str(rule.id),
                "name": rule.name,
                "metric": rule.metric,
                "operator": rule.operator,
                "threshold": float(rule.threshold),
                "duration_seconds": float(rule.duration_seconds),
                "cooldown_seconds": float(rule.cooldown_seconds),
                "severity": str(rule.severity),
                "enabled": bool(rule.enabled),
            }
            self._states = {
                key: state for key, state in self._states.items() if key[0] != str(rule.id)
            }

    def remove_rule(self, rule_id: str) -> None:
        with self._lock:
            self._rules.pop(rule_id, None)
            self._states = {
                key: state for key, state in self._states.items() if key[0] != rule_id
            }

    def evaluate(self, event: object) -> list[RuleTransition]:
        """Evaluate every enabled rule for one telemetry event."""
        transitions: list[RuleTransition] = []
        host_key = str(getattr(event, "host_id", None) or "system")
        timestamp = getattr(event, "timestamp", None) or datetime.now(timezone.utc)

        with self._lock:
            for rule in self._rules.values():
                if not bool(rule["enabled"]):
                    continue

                self.evaluations += 1
                metric = str(rule["metric"])
                value = float(getattr(event, metric))
                key = (str(rule["id"]), host_key)
                state = self._states.setdefault(key, RuleState())
                condition_met = _matches(value, str(rule["operator"]), float(rule["threshold"]))

                if condition_met:
                    if state.condition_since is None:
                        state.condition_since = timestamp

                    duration_met = (
                        timestamp - state.condition_since
                    ).total_seconds() >= float(rule["duration_seconds"])
                    cooldown_met = (
                        state.last_fired_at is None
                        or (timestamp - state.last_fired_at).total_seconds()
                        >= float(rule["cooldown_seconds"])
                    )

                    if state.active_alert_id is None and duration_met and cooldown_met:
                        alert = Alert(
                            id=uuid4().hex,
                            timestamp=timestamp,
                            metric=metric,
                            value=value,
                            baseline=float(rule["threshold"]),
                            severity=str(rule["severity"]),
                            message=(
                                f"{rule['name']}: {metric} {rule['operator']} "
                                f"{rule['threshold']} (observed {value:.2f})"
                            ),
                            host_id=getattr(event, "host_id", None),
                            source="rule",
                            rule_id=str(rule["id"]),
                        )
                        state.active_alert_id = alert.id
                        state.last_fired_at = timestamp
                        self.fires += 1
                        transitions.append(RuleTransition(alert=alert, action="created"))
                else:
                    state.condition_since = None
                    if state.active_alert_id is not None:
                        alert = Alert(
                            id=state.active_alert_id,
                            timestamp=timestamp,
                            metric=metric,
                            value=value,
                            baseline=float(rule["threshold"]),
                            severity=str(rule["severity"]),
                            message=(
                                f"{rule['name']}: {metric} returned to normal "
                                f"(observed {value:.2f})"
                            ),
                            resolved=True,
                            resolved_at=timestamp,
                            host_id=getattr(event, "host_id", None),
                            source="rule",
                            rule_id=str(rule["id"]),
                        )
                        state.active_alert_id = None
                        self.resolutions += 1
                        transitions.append(RuleTransition(alert=alert, action="resolved"))

        return transitions

    def reset(self) -> None:
        with self._lock:
            self._states.clear()
