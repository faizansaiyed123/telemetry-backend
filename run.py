#!/usr/bin/env python3
"""Telemetry backend development launcher.

Starts only backend services:
  1. Backend API      (uvicorn, port 8000)
  2. Webhook receiver (port 8787)

Does NOT start the frontend — frontend and backend are in separate repositories.

Usage:
  python run.py start   Start backend services
  python run.py stop    Stop backend services
  python run.py restart   Restart backend services
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
PID_FILE = BACKEND_DIR / ".telemetry.pids.json"

WEBHOOK_SECRET = os.environ.get(
    "WEBHOOK_SIGNING_SECRET", "local-dev-webhook-signing-secret-32chars"
)


def _kill_pid(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _kill_port(port):
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if f"LISTENING" in line and f":{port} " in line:
                parts = line.split()
                if parts:
                    pid_str = parts[-1]
                    if pid_str.isdigit():
                        _kill_pid(int(pid_str))
    except Exception:
        pass


def stop_all():
    if PID_FILE.exists():
        try:
            pids = json.loads(PID_FILE.read_text())
            for entry in pids:
                _kill_pid(entry.get("pid"))
            PID_FILE.unlink()
        except Exception:
            pass

    for port in (8000, 8787):
        _kill_port(port)

    print("Backend services stopped.")


def start_all():
    if PID_FILE.exists():
        print("Services already running (pid file exists). Use 'stop' first or 'restart'.")
        return

    if not VENV_PYTHON.exists():
        print(
            f"Backend venv not found at {VENV_PYTHON}. Run 'uv sync' in backend/telemetry-backend first.",
            file=sys.stderr,
        )
        sys.exit(1)

    os.environ["WEBHOOK_SIGNING_SECRET"] = WEBHOOK_SECRET

    pids = []

    be = subprocess.Popen(
        [str(VENV_PYTHON), "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"],
        cwd=str(BACKEND_DIR),
    )
    pids.append({"name": "backend", "pid": be.pid})
    print(f"Backend  started (pid {be.pid}) :8000")

    wr = subprocess.Popen(
        [str(VENV_PYTHON), "examples/webhook_receiver.py"],
        cwd=str(BACKEND_DIR),
        env={**os.environ, "WEBHOOK_SIGNING_SECRET": WEBHOOK_SECRET},
    )
    pids.append({"name": "webhook", "pid": wr.pid})
    print(f"Webhook  started (pid {wr.pid}) :8787")

    PID_FILE.write_text(json.dumps(pids, indent=2))
    print(f"\nBackend services running. Run 'python run.py stop' to stop them.")


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
    else:
        cmd = "start"

    if cmd == "stop":
        stop_all()
    elif cmd == "restart":
        stop_all()
        time.sleep(2)
        start_all()
    else:
        start_all()


if __name__ == "__main__":
    main()
