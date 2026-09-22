"""In-memory rule evaluator for sustained telemetry conditions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from app.models.observability import AlertRuleRecord
from app.models.telemetry import TelemetryEvent

SUPPORTED_METRICS = {
    "cpu",
    "memory",
    "temperature",
    "network_mbps",
    "requests_per_second",
    "error_rate",
    "latency_ms",
}
SUPPORTED_OPERATORS = {"gt", "gte", "lt", "lte", "abs_gt"}


@dataclass(slots=True)
class RuleState:
    condition_started_at: datetime | None = None
    active_alert_id: str | None = None
    last_fired_at: datetime | None = None
    last_value: float | None = None


@dataclass(frozen=True, slots=True)
class RuleAction:
    action: str
    rule: AlertRuleRecord
    event: TelemetryEvent
    alert_id: str | None = None


class RuleEngine:
    """Evaluate configured rules without database work per telemetry event."""

    def __init__(self) -> None:
        self._rules: dict[str, AlertRuleRecord] = {}
        self._states: dict[tuple[str, str], RuleState] = {}
        self.evaluations = 0
        self.fires = 0
        self.resolves = 0

    @property
    def rules(self) -> list[AlertRuleRecord]:
        return list(self._rules.values())

    @property
    def enabled_rule_count(self) -> int:
        return len(self._rules)

    @property
    def active_rule_alert_count(self) -> int:
        return sum(state.active_alert_id is not None for state in self._states.values())

    def load(self, rules: list[AlertRuleRecord]) -> None:
        rule_map = {rule.id: rule for rule in rules if rule.enabled}
        self._rules = rule_map
        self._states = {
            key: state for key, state in self._states.items() if key[0] in rule_map
        }

    def reset_state(self) -> None:
        self._states.clear()

    def evaluate(self, event: TelemetryEvent) -> list[RuleAction]:
        actions: list[RuleAction] = []
        self.evaluations += len(self._rules)
        now = event.timestamp

        for rule in self._rules.values():
            if rule.host_id is not None and rule.host_id != event.host_id:
                continue

            value = float(getattr(event, rule.metric))
            key = (rule.id, event.host_id or "__global__")
            state = self._states.setdefault(key, RuleState())
            state.last_value = value

            if self._matches(rule.operator, value, rule.threshold):
                if state.condition_started_at is None:
                    state.condition_started_at = now

                elapsed = max(0.0, (now - state.condition_started_at).total_seconds())
                cooldown_ok = (
                    state.last_fired_at is None
                    or (now - state.last_fired_at).total_seconds() >= rule.cooldown_seconds
                )

                if (
                    state.active_alert_id is None
                    and elapsed >= rule.duration_seconds
                    and cooldown_ok
                ):
                    state.active_alert_id = f"rule-alert-{uuid4()}"
                    state.last_fired_at = now
                    self.fires += 1
                    actions.append(
                        RuleAction(
                            action="fire",
                            rule=rule,
                            event=event,
                            alert_id=state.active_alert_id,
                        )
                    )
            else:
                state.condition_started_at = None
                if state.active_alert_id is not None:
                    alert_id = state.active_alert_id
                    state.active_alert_id = None
                    self.resolves += 1
                    actions.append(
                        RuleAction(
                            action="resolve",
                            rule=rule,
                            event=event,
                            alert_id=alert_id,
                        )
                    )

        return actions

    def active_alert_ids_for_rule(self, rule_id: str) -> list[str]:
        return [
            state.active_alert_id
            for (current_rule_id, _), state in self._states.items()
            if current_rule_id == rule_id and state.active_alert_id is not None
        ]

    def disable_rule(self, rule_id: str) -> list[str]:
        alert_ids = self.active_alert_ids_for_rule(rule_id)
        self._rules.pop(rule_id, None)
        self._states = {
            key: state for key, state in self._states.items() if key[0] != rule_id
        }
        return alert_ids

    @staticmethod
    def _matches(operator: str, value: float, threshold: float) -> bool:
        if operator == "gt":
            return value > threshold
        if operator == "gte":
            return value >= threshold
        if operator == "lt":
            return value < threshold
        if operator == "lte":
            return value <= threshold
        if operator == "abs_gt":
            return abs(value) > abs(threshold)
        raise ValueError(f"Unsupported alert-rule operator: {operator}")
