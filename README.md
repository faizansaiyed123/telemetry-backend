# Telemetry Backend

FastAPI service for a real-time infrastructure observability platform. It generates telemetry, detects anomalies, manages alerts, persists historical data in PostgreSQL, and streams live events to authenticated clients over WebSocket.

## What this service provides

- Real-time telemetry generation for CPU, memory, temperature, network throughput, requests/sec, error rate, and latency.
- Rolling z-score anomaly detection with INFO, WARNING, and CRITICAL severity levels.
- Alert lifecycle tracking: detected → active → resolved, with deduplication and acknowledgement.
- PostgreSQL persistence through SQLAlchemy and Alembic.
- JWT authentication with Argon2 password hashing.
- Public account signup with viewer access by default.
- Role-based access control for admin, operator, and viewer.
- Host and user administration APIs.
- Simulation controls for start, pause, resume, reset, rate changes, and fault injection.
- Authenticated WebSocket streaming for telemetry, alerts, and system events.
- Bounded background persistence queues so database work stays off the telemetry generation path.
- Liveness and readiness endpoints.
- Production configuration checks and a non-root Docker runtime.

Redis is intentionally not used in the current architecture. The application is designed as a self-contained single-process service.

## Architecture

```
                    ┌─────────────────────────┐
                    │      FastAPI app        │
                    └────────────┬────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            │                    │                    │
            ▼                    ▼                    ▼
      REST API layer       WebSocket layer      Health checks
            │                    │
            ▼                    ▼
     ┌──────────────┐     ┌───────────────┐
     │ Telemetry    │────▶│ WebSocket     │
     │ Manager      │     │ Manager       │
     └──────┬───────┘     └───────────────┘
            │
      ┌─────┼───────────────┐
      │     │               │
      ▼     ▼               ▼
 Generator  Anomaly      Persistence
            detector      workers
                              │
                              ▼
                         PostgreSQL
```

The TelemetryManager is the central runtime component. It owns the live telemetry state, generation lifecycle, anomaly processing, alert lifecycle, and persistence workers.

## Tech stack

| Area | Technology |
|---|---|
| API | FastAPI |
| Language | Python 3.12+ |
| Recommended runtime | Python 3.13 |
| Database | PostgreSQL 16+ |
| ORM | SQLAlchemy 2 |
| Migrations | Alembic |
| Authentication | JWT / PyJWT |
| Password hashing | Argon2 via pwdlib |
| Validation | Pydantic v2 |
| Streaming | WebSocket |
| Runtime | Uvicorn |
| Package management | uv |
| Container | Docker |

## Project structure

```
telemetry-backend/
├── app/
│   ├── api/              # HTTP and WebSocket endpoints
│   ├── core/             # settings, security, bootstrap, logging
│   ├── db/               # SQLAlchemy engine and sessions
│   ├── models/           # API/domain and database models
│   ├── services/         # telemetry engine, anomaly detection, persistence
│   └── utils/            # shared helpers
├── alembic/              # database migrations
├── tests/
│   ├── unit/
│   └── integration/
├── .env.example
├── Dockerfile
├── README.md
├── pyproject.toml
└── uv.lock
```

## Requirements

Install:

- Python 3.12 or newer
- PostgreSQL 16 or newer
- uv

A local Redis server is not required.

## Local setup

### 1. Install dependencies

```
uv sync
```

### 2. Configure the environment

Copy the example file:

```
cp .env.example .env
```

At minimum, configure the database and authentication values for your environment.

### 3. Create or update the database schema

Alembic owns schema creation. The application does not create tables automatically.

```
uv run alembic upgrade head
```

### 4. Start the API

```
uv run uvicorn app.main:app --reload
```

The API is available at:

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Environment configuration

See .env.example for the complete list.

| Variable | Default | Purpose |
|---|---|---|
| APP_NAME | Telemetry Backend | API name |
| APP_ENV | development | Runtime environment |
| HOST | 0.0.0.0 | Bind host |
| PORT | 8000 | Bind port |
| LOG_LEVEL | INFO | Log level |
| DATABASE_URL | local PostgreSQL URL | PostgreSQL connection |
| DATABASE_ECHO | false | SQLAlchemy SQL logging |
| JWT_SECRET_KEY | development placeholder | JWT signing secret |
| JWT_ALGORITHM | HS256 | JWT algorithm |
| ACCESS_TOKEN_EXPIRE_MINUTES | 60 | JWT lifetime |
| BOOTSTRAP_ADMIN_EMAIL | admin@example.com | Initial administrator email |
| BOOTSTRAP_ADMIN_PASSWORD | development placeholder | Initial administrator password |
| TELEMETRY_RATE | 10 | Default events/sec |
| MAX_TELEMETRY_RATE | 100 | Maximum allowed events/sec |
| MAX_HISTORY_SIZE | 5000 | In-memory history limit |
| TELEMETRY_PERSISTENCE_ENABLED | true | Enable PostgreSQL telemetry persistence |
| TELEMETRY_HOST_NAME | synthetic-local | System-managed persistence host |
| TELEMETRY_HOST_ENVIRONMENT | development | Persistence host environment |
| ANOMALY_Z_THRESHOLD | 3.0 | Anomaly detection threshold |
| CORS_ALLOWED_ORIGINS | localhost origins | Comma-separated browser origins |

### Production configuration

Set:

```
APP_ENV=production
```

Production startup rejects the known development JWT secret or bootstrap password and enforces minimum secret lengths.

Use strong, unique values for:

```
JWT_SECRET_KEY=<long-random-secret>
BOOTSTRAP_ADMIN_PASSWORD=<strong-initial-password>
```

The bootstrap admin is created only when the configured email does not already exist. Changing BOOTSTRAP_ADMIN_PASSWORD later does not overwrite an existing administrator password.

## Authentication and roles

Authentication uses bearer JWTs.

### Public signup

`POST /api/auth/signup` accepts an email and password without authentication. The API normalizes the email, stores a securely hashed password, creates the account as an active `viewer`, and returns a bearer session using the same response contract as login. Existing emails return `409 Conflict`.

This keeps public registration separate from the admin-only user-management API: administrators can still create or update accounts and can grant operator/admin roles.

### Roles

| Capability | Viewer | Operator | Admin |
|---|:---:|:---:|:---:|
| Read telemetry | ✓ | ✓ | ✓ |
| Read alerts | ✓ | ✓ | ✓ |
| Acknowledge alerts |  | ✓ | ✓ |
| Simulation controls |  | ✓ | ✓ |
| Read hosts | ✓ | ✓ | ✓ |
| Create/update/delete hosts |  |  | ✓ |
| Manage users |  |  | ✓ |

The backend checks both the JWT and the current active database user. Deactivating a user therefore blocks access even when a previously issued token has not yet expired.

## REST API

### Health

| Method | Endpoint | Access | Description |
|---|---|---|---|
| GET | /health | Public | Liveness information |
| GET | /ready | Public | Database/application readiness |

### Authentication

| Method | Endpoint | Access | Description |
|---|---|---|---|
| POST | /api/auth/login | Public | Issue a bearer token |
| POST | /api/auth/signup | Public | Create a viewer account and issue a bearer token |
| GET | /api/auth/me | Authenticated | Return current user |
| POST | /api/auth/change-password | Authenticated | Change current password |

### Users

| Method | Endpoint | Access | Description |
|---|---|---|---|
| GET | /api/users | Admin | List users |
| POST | /api/users | Admin | Create user |
| PATCH | /api/users/{user_id} | Admin | Change role, status, or password |

The backend prevents an administrator from removing their own admin access and prevents the last active administrator account from being removed.

### Hosts

| Method | Endpoint | Access | Description |
|---|---|---|---|
| GET | /api/hosts | Authenticated | List hosts |
| POST | /api/hosts | Admin | Create host |
| PATCH | /api/hosts/{host_id} | Admin | Update host |
| DELETE | /api/hosts/{host_id} | Admin | Delete host |

The configured synthetic persistence host is system-managed while persistence is enabled.

### Telemetry

| Method | Endpoint | Access | Description |
|---|---|---|---|
| GET | /api/telemetry/current | Authenticated | Latest telemetry event |
| GET | /api/telemetry/history?limit=100 | Authenticated | Persistent history, with in-memory fallback |
| GET | /api/telemetry/stats | Authenticated | Backend-computed aggregate statistics |

### Alerts

| Method | Endpoint | Access | Description |
|---|---|---|---|
| GET | /api/alerts?active_only=false | Authenticated | Alert history |
| POST | /api/alerts/{alert_id}/acknowledge | Operator/Admin | Acknowledge an alert |

### Simulation

| Method | Endpoint | Access | Description |
|---|---|---|---|
| GET | /api/simulation/status | Authenticated | Current engine state |
| POST | /api/simulation/start | Operator/Admin | Start generation |
| POST | /api/simulation/pause | Operator/Admin | Pause generation |
| POST | /api/simulation/resume | Operator/Admin | Resume generation |
| POST | /api/simulation/reset | Operator/Admin | Clear runtime and persisted synthetic state |
| POST | /api/simulation/rate?rate=N | Operator/Admin | Set generation rate |
| POST | /api/simulation/trigger | Operator/Admin | Inject a controlled anomaly |

Supported anomaly metrics:

```
cpu
memory
temperature
latency
error_rate
```

## WebSocket

Endpoint:

```
ws://localhost:8000/ws/telemetry?token=<jwt>
```

Use wss:// behind HTTPS.

The WebSocket requires a valid JWT for an active database user.

### Message types

Telemetry:

```
{
  "type": "telemetry",
  "data": {
    "timestamp": "2026-09-18T10:00:00.000Z",
    "sequence": 123,
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

Alert:

```
{
  "type": "alert",
  "data": {
    "id": "alert-1",
    "timestamp": "2026-09-18T10:00:01.000Z",
    "metric": "cpu",
    "value": 95.2,
    "baseline": 62.1,
    "severity": "CRITICAL",
    "message": "cpu anomaly detected",
    "resolved": false,
    "resolved_at": null,
    "acknowledged": false
  }
}
```

System:

```
{
  "type": "system",
  "data": {
    "event": "rate_changed",
    "message": "Telemetry rate changed to 50/sec"
  }
}
```

System events cover lifecycle changes such as pause, resume, reset, rate changes, and anomaly triggers.

## Telemetry and anomaly behavior

Telemetry is generated from bounded, correlated synthetic signals rather than real host agents.

The anomaly detector keeps a rolling window for each monitored metric:

```
z = (value - mean) / standard_deviation
```

Detection starts only after enough history has been collected. The defaults are:

- minimum history: 30 events
- rolling window: 100 events
- threshold: 3.0

An anomaly above threshold is WARNING. A z-score more than twice the configured threshold is CRITICAL.

## Persistence design

Telemetry and alert persistence run asynchronously:

1. The generation loop creates an event.
2. The event is added to bounded in-memory state.
3. The persistence worker receives it through a bounded queue.
4. Database writes are performed in short-lived batches.
5. Alert lifecycle changes are persisted independently.
6. Failed persistence work is retained for retry.

This keeps PostgreSQL latency out of the main telemetry generation path.

Persistence can be disabled with:

```
TELEMETRY_PERSISTENCE_ENABLED=false
```

The live simulation still works in memory when persistence is disabled.

## Database migrations

Apply migrations:

```
uv run alembic upgrade head
```

Show current migration:

```
uv run alembic current
```

Create a migration after model changes:

```
uv run alembic revision --autogenerate -m "describe the change"
```

Review autogenerated migrations before committing them.

## Testing

Run the full suite:

```
uv run pytest -q
```

The test suite covers authentication, configuration security, database models, authorization, aggregation, anomaly detection, telemetry generation, persistence, manager lifecycle, health/readiness, simulation controls, REST APIs, WebSocket streaming, and alert lifecycle behavior.

CI also applies Alembic migrations against PostgreSQL before running tests.

## Docker

Build:

```
docker build -t telemetry-backend .
```

Run:

```
docker run --rm \
  -p 8000:8000 \
  --env-file .env \
  telemetry-backend
```

The image:

- uses Python 3.13
- installs production dependencies only
- excludes test sources
- runs as a non-root appuser
- exposes port 8000
- includes a /health Docker health check

Run database migrations separately before starting the application.

## CI

GitHub Actions runs on pushes and pull requests targeting main.

The backend workflow:

1. starts PostgreSQL 16
2. installs dependencies with uv
3. applies Alembic migrations
4. runs the full test suite
5. verifies migration state

## Operational notes

This backend is intentionally a single-process V1 simulation service.

That means:

- one process owns the live telemetry state
- all connected WebSocket clients receive the same generated stream
- runtime state is local to the process
- PostgreSQL provides durable telemetry and alert history when persistence is enabled
- Redis and a message broker are not required

The architecture can later be extended with real telemetry agents, multiple workers, external queues, and distributed stream processing without changing the public dashboard concepts.

## License

MIT
