"""Regression tests for FastAPI route registration."""

from fastapi.routing import APIRoute

from app.main import create_app


EXPECTED_HTTP_ROUTES = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/signup"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/change-password"),
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PATCH", "/api/users/{user_id}"),
    ("GET", "/api/hosts"),
    ("POST", "/api/hosts"),
    ("PATCH", "/api/hosts/{host_id}"),
    ("DELETE", "/api/hosts/{host_id}"),
    ("GET", "/api/telemetry/current"),
    ("GET", "/api/telemetry/history"),
    ("GET", "/api/telemetry/stats"),
    ("GET", "/api/alerts"),
    ("POST", "/api/alerts/{alert_id}/acknowledge"),
    ("GET", "/api/simulation/status"),
    ("POST", "/api/simulation/start"),
    ("POST", "/api/simulation/pause"),
    ("POST", "/api/simulation/resume"),
    ("POST", "/api/simulation/reset"),
    ("POST", "/api/simulation/rate"),
    ("POST", "/api/simulation/trigger"),
    ("GET", "/api/api-keys"),
    ("POST", "/api/api-keys/hosts/{host_id}"),
    ("POST", "/api/api-keys/{key_id}/revoke"),
    ("POST", "/api/ingest/v1/telemetry"),
    ("GET", "/api/alert-rules"),
    ("POST", "/api/alert-rules"),
    ("PATCH", "/api/alert-rules/{rule_id}"),
    ("DELETE", "/api/alert-rules/{rule_id}"),
    ("GET", "/api/incidents"),
    ("GET", "/api/incidents/{incident_id}"),
    ("POST", "/api/incidents/{incident_id}/acknowledge"),
    ("GET", "/api/observability/metrics"),
    ("GET", "/api/observability/metrics/prometheus"),
    ("GET", "/api/observability/audit-logs"),
    ("GET", "/api/slos"),
    ("GET", "/api/slos/{slo_id}/status"),
    ("POST", "/api/slos"),
    ("PATCH", "/api/slos/{slo_id}"),
    ("DELETE", "/api/slos/{slo_id}"),
}


def test_all_expected_http_routes_are_registered() -> None:
    app = create_app()
    registered = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert EXPECTED_HTTP_ROUTES <= registered


def test_telemetry_websocket_route_is_registered() -> None:
    app = create_app()
    websocket_paths = {route.path for route in app.routes if route.__class__.__name__ == "APIWebSocketRoute"}
    assert "/ws/telemetry" in websocket_paths


def test_no_duplicate_http_method_paths_are_registered() -> None:
    app = create_app()
    seen = set()
    duplicates = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            key = (method, route.path)
            if key in seen:
                duplicates.append(key)
            seen.add(key)
    assert duplicates == []
