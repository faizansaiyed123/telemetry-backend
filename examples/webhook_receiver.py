"""Local webhook receiver for development and interview demos.

Usage:
    set WEBHOOK_SIGNING_SECRET=...
    uv run python examples/webhook_receiver.py

The receiver verifies the platform's timestamped HMAC-SHA256 signature and
rejects stale or malformed deliveries. It uses only Python's standard library.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


SECRET = os.environ.get("WEBHOOK_SIGNING_SECRET", "")
MAX_AGE_SECONDS = 300


def verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    if not SECRET or not timestamp or not signature:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - sent_at) > MAX_AGE_SECONDS:
        return False

    expected = "sha256=" + hmac.new(
        SECRET.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class Handler(BaseHTTPRequestHandler):
    server_version = "TelemetryWebhookReceiver/1.0"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        timestamp = self.headers.get("X-Telemetry-Timestamp", "")
        signature = self.headers.get("X-Telemetry-Signature", "")

        if not verify_signature(body, timestamp, signature):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"invalid signature")
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"invalid json")
            return

        print(
            json.dumps(
                {
                    "event": self.headers.get("X-Telemetry-Event"),
                    "delivery": self.headers.get("X-Telemetry-Delivery"),
                    "payload": payload,
                },
                indent=2,
            )
        )
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(format % args)


if __name__ == "__main__":
    if len(SECRET) < 32:
        raise SystemExit("WEBHOOK_SIGNING_SECRET must be at least 32 characters")
    print("Listening on http://127.0.0.1:8787/webhook")
    HTTPServer(("127.0.0.1", 8787), Handler).serve_forever()
