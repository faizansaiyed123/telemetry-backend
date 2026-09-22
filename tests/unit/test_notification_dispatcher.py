"""Unit tests for notification routing and webhook safety."""

from types import SimpleNamespace

import pytest

from app.services.notification_dispatcher import (
    _channel_accepts,
    validate_webhook_url,
)


def channel(**overrides):
    values = {
        "enabled": True,
        "channel_type": "webhook",
        "notify_alerts": False,
        "notify_incidents": True,
        "min_severity": "WARNING",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_incident_channel_accepts_warning_and_critical():
    assert _channel_accepts(channel(), "incident", "WARNING")
    assert _channel_accepts(channel(), "incident", "CRITICAL")


def test_min_severity_filters_lower_priority_events():
    assert not _channel_accepts(channel(min_severity="CRITICAL"), "incident", "WARNING")
    assert _channel_accepts(channel(min_severity="CRITICAL"), "incident", "CRITICAL")


def test_event_type_toggle_is_enforced():
    assert not _channel_accepts(channel(notify_alerts=False), "alert", "CRITICAL")
    assert _channel_accepts(channel(notify_alerts=True), "alert", "CRITICAL")
    assert not _channel_accepts(channel(notify_incidents=False), "incident", "CRITICAL")


def test_disabled_or_unknown_channel_type_is_rejected():
    assert not _channel_accepts(channel(enabled=False), "incident", "CRITICAL")
    assert not _channel_accepts(channel(channel_type="email"), "incident", "CRITICAL")


def test_webhook_requires_https():
    with pytest.raises(ValueError, match="HTTPS"):
        validate_webhook_url("http://example.com/hook")


def test_webhook_rejects_local_literal_ip():
    with pytest.raises(ValueError, match="public IP"):
        validate_webhook_url("https://127.0.0.1/hook")


def test_webhook_rejects_credentials():
    with pytest.raises(ValueError, match="credentials"):
        validate_webhook_url("https://user:password@example.com/hook")


def test_webhook_rejects_private_dns_resolution(monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_dispatcher.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("10.0.0.5", 443)),
        ],
    )
    with pytest.raises(ValueError, match="private or reserved"):
        validate_webhook_url("https://hooks.example.com/hook")


def test_webhook_accepts_public_dns_resolution(monkeypatch):
    monkeypatch.setattr(
        "app.services.notification_dispatcher.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("203.0.113.20", 443)),
        ],
    )
    # Documentation-only address space is intentionally not globally routable,
    # so use a known public address for the positive safety-path test.
    monkeypatch.setattr(
        "app.services.notification_dispatcher.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("1.1.1.1", 443)),
        ],
    )
    validate_webhook_url("https://hooks.example.com/hook")
