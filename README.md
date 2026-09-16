# Telemetry Backend

Real-time telemetry dashboard backend built with FastAPI. Generates realistic, correlated system metrics and streams them to multiple WebSocket clients simultaneously. Includes REST APIs for querying history, statistics, and controlling the simulation.

## Overview

The backend simulates a server monitoring system that produces telemetry events (CPU, memory, temperature, network, requests/sec, error rate, latency) at configurable rates. A single central generator produces one stream of telemetry that is broadcast to all connected WebSocket clients. Deterministic rolling z-score anomaly detection flags unusual values, and an alert system manages the lifecycle of detected anomalies (detected → active → resolved).

## Architecture

### Central Telemetry Generator
One background task generates telemetry events at the configured rate. All WebSocket clients receive the same stream — no per-client generation.

### Telemetry Manager
Owns the bounded history (`collections.deque`), sequence counter, simulation state (running/paused), rate, and active anomaly triggers. Acts as the central hub between the generator and all consumers.

### WebSocket Manager
Manages client connections: register, unregister, broadcast. A failed client is removed without affecting other clients. Safe under asyncio concurrency.

### REST API
Standard FastAPI endpoints for current telemetry, history, statistics, alerts, and simulation controls (start, pause, resume, reset, rate, trigger anomaly).

### Anomaly Detector
Deterministic rolling z-score: `z = (value - mean) / std_dev`. No ML, no external services. Threshold configurable via `ANOMALY_Z_THRESHOLD`.

### Alert System
Alerts have a lifecycle (detected → active → resolved). Deduplication prevents alert storms during continuous anomalies. Alerts resolve when metrics return to normal.

### Bounded In-Memory State
All state lives in memory with bounded structures. No database, no Redis, no message broker. Data is lost on restart — by design for V1.

## Requirements

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) for dependency management
- Docker (optional)

## Installation

```bash
uv sync
```

## Development

Run the development server with auto-reload:

```bash
uv run uvicorn app.main:app --reload
```

## Tests

```bash
uv run pytest
```

## API Documentation

Once the server is running:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check with uptime, stream status, client count |
| GET | `/api/telemetry/current` | Latest telemetry event |
| GET | `/api/telemetry/history` | Bounded telemetry history (query: `limit`) |
| GET | `/api/telemetry/stats` | Aggregated statistics over history |
| GET | `/api/alerts` | Active and recent alerts |
| GET | `/api/simulation/status` | Current simulation state |
| POST | `/api/simulation/start` | Start telemetry generation |
| POST | `/api/simulation/pause` | Pause telemetry generation |
| POST | `/api/simulation/resume` | Resume telemetry generation |
| POST | `/api/simulation/reset` | Reset all telemetry state |
| POST | `/api/simulation/rate` | Set telemetry rate (query: `rate`) |
| POST | `/api/simulation/trigger` | Trigger an anomaly (body: `{"metric": "cpu"}`) |

## WebSocket

Connect to `ws://localhost:8000/ws/telemetry`

### Message Types

**telemetry** — streamed telemetry event:
```json
{
  "type": "telemetry",
  "data": {
    "timestamp": "2026-09-16T10:00:00.123Z",
    "sequence": 1234,
    "cpu": 63.4,
    "memory": 71.2,
    "temperature": 48.1,
    "network_mbps": 82.4,
    "requests_per_second": 421,
    "error_rate": 0.8,
    "latency_ms": 38.4
  }
}
```

**alert** — anomaly alert:
```json
{
  "type": "alert",
  "data": {
    "id": "alert-1",
    "timestamp": "2026-09-16T10:00:01.000Z",
    "metric": "cpu",
    "value": 95.2,
    "baseline": 62.1,
    "severity": "CRITICAL",
    "message": "CPU anomaly detected: 95.2% (baseline: 62.1%)",
    "resolved": false
  }
}
```

**system** — system status messages (pause, resume, reset, rate change):
```json
{
  "type": "system",
  "data": {
    "event": "paused",
    "message": "Telemetry generation paused"
  }
}
```

## Configuration

All settings have sensible defaults and work without a `.env` file. Copy `.env.example` to `.env` to customize:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | Telemetry Backend | Application name |
| `APP_ENV` | development | Environment name |
| `HOST` | 0.0.0.0 | Bind host |
| `PORT` | 8000 | Bind port |
| `LOG_LEVEL` | INFO | Logging level |
| `TELEMETRY_RATE` | 10 | Events per second |
| `MAX_TELEMETRY_RATE` | 100 | Maximum allowed rate |
| `MAX_HISTORY_SIZE` | 5000 | Maximum history events retained |
| `ANOMALY_Z_THRESHOLD` | 3.0 | Z-score threshold for anomaly detection |
| `CORS_ALLOWED_ORIGINS` | http://localhost:5173,http://localhost:3000 | Comma-separated allowed CORS origins |

## Docker

```bash
docker build -t telemetry-backend .
docker run --rm -p 8000:8000 telemetry-backend
```

## Architecture Decisions

**One central generator**: A single background task produces one telemetry stream broadcast to all clients. This ensures all clients see the same data and avoids per-client resource duplication.

**In-memory bounded history**: Using `collections.deque(maxlen=...)` keeps memory bounded without database complexity. Appropriate for a real-time monitoring simulation where historical persistence is not required.

**WebSockets**: Real-time push to clients is the natural fit for telemetry streaming. Polling REST endpoints would introduce unnecessary latency and load.

**Deterministic anomaly detection**: Rolling z-score is simple, fast, deterministic, and testable. No ML or external services needed.

**No database / no Redis / no message broker**: V1 is a self-contained simulation. Adding infrastructure would add complexity without value at this stage.

## Limitations

- **In-memory state**: All telemetry history, alerts, and state are lost on restart.
- **Simulation**: Telemetry is generated, not collected from real systems.
- **Single process**: Not distributed; one process handles all clients.
- **No authentication**: No auth or authorization is implemented in V1.
- **No persistence**: No database or persistent storage layer.
