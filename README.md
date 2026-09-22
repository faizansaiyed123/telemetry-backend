# Telemetry Backend

Production-oriented FastAPI backend for a real-time infrastructure observability platform.

The service combines live telemetry streaming, real host ingestion, deterministic anomaly detection, configurable alert rules, incident correlation, SLO/error-budget evaluation, PostgreSQL persistence, auditability, abuse protection, and self-observability. It uses only free/open-source components and keeps the real-time path independent from database latency.

## Why this project is technically interesting

This is intentionally more than a dashboard API. The backend demonstrates several engineering problems that appear in real observability systems:

- **Two telemetry sources through one processing pipeline:** a correlated synthetic generator for repeatable demos and a real host agent built on psutil.
- **Hot-path isolation:** telemetry generation and WebSocket broadcast do not wait on PostgreSQL writes.
- **Durable ingestion:** host-scoped API keys, bounded batches, PostgreSQL uniqueness, and idempotent retry behavior.
- **Stateful alerting:** threshold rules support sustained-duration conditions, cooldowns, deduplication, and per-host state.
- **Incident correlation:** related alerts are grouped into incidents and persisted asynchronously.
- **SLOs:** database-side aggregation calculates sample-based SLI and remaining error budget.
- **Self-observability:** runtime health, throughput, queue depth, drops, request RED metrics, and Prometheus-compatible output.
- **Security controls:** JWT + active-user checks, Argon2 password hashing, API-key hashing, role-based authorization, audit logs, and rate limiting.
- **Explicit scaling boundary:** the current deployment is single-process; process-local state is not disguised as distributed state.

## Architecture

```
                         ┌──────────────────────────┐
                         │        React UI           │
                         │ REST + authenticated WS   │
                         └────────────┬─────────────┘
                                      │
                         ┌────────────▼─────────────┐
                         │        FastAPI API        │
                         │ auth / RBAC / rate limit  │
                         └────────────┬─────────────┘
                                      │
             ┌────────────────────────┼────────────────────────┐
             │                        │                        │
             ▼                        ▼                        ▼
     Synthetic generator       Real host agent          Query / admin APIs
             │                 (psutil + HTTPS)                │
             └──────────────┬───────────────┬──────────────────┘
                            ▼               ▼
                    ┌────────────────────────────┐
                    │      TelemetryManager       │
                    │ unified processing pipeline  │
                    └─────────────┬──────────────┘
                                  │
          ┌───────────────────────┼────────────────────────┐
          │                       │                        │
          ▼                       ▼                        ▼
   Anomaly detector        Alert rule engine        Runtime metrics
          │                       │                        │
          └──────────────┬────────┘                        │
                         ▼                                 │
                ┌─────────────────┐                        │
                │ Incident engine │                        │
                └────────┬────────┘                        │
                         │                                 │
                         ▼                                 ▼
                 Async persistence                    /metrics
                         │                         Prometheus text
                         ▼
                    PostgreSQL
```

### Core design principle

All telemetry sources enter the same `TelemetryManager.process_event()` pipeline.

That means anomaly detection, rule evaluation, alert lifecycle, incident correlation, WebSocket publication, and runtime counters behave consistently regardless of whether an event came from the simulator or a real host.

Database persistence is deliberately asynchronous and bounded. If PostgreSQL slows down, the real-time generation path is not held hostage by a synchronous database transaction.

## Tech stack

| Area | Technology |
|---|---|
| API | FastAPI |
| Language | Python 3.12+ |
| Runtime | Uvicorn |
| Validation | Pydantic v2 |
| Database | PostgreSQL 16+ |
| ORM | SQLAlchemy 2 |
| Migrations | Alembic |
| Authentication | JWT / PyJWT |
| Password hashing | Argon2 via pwdlib |
| Real host metrics | psutil |
| Streaming | WebSocket |
| Package manager | uv |
| Containers | Docker |
| Testing | pytest + pytest-asyncio + httpx |

No Redis, paid SaaS, paid observability platform, or external broker is required.

## Project structure

```
telemetry-backend/
├── app/
│   ├── api/              # REST and WebSocket endpoints
│   ├── core/             # settings, security, bootstrap, rate limiting
│   ├── db/               # SQLAlchemy engine/session
│   ├── models/           # API and database models
│   ├── services/         # telemetry, ingestion, rules, incidents, SLOs
│   └── utils/            # shared helpers
├── agent/
│   ├── telemetry_agent.py
│   └── README.md
├── alembic/
│   └── versions/
├── tests/
│   ├── unit/
│   └── integration/
├── Dockerfile
├── README.md
├── pyproject.toml
└── uv.lock
```

## Local setup

Requirements:

- Python 3.12+
- PostgreSQL 16+
- uv
- Docker is optional for local development but used by CI

Install:

```bash
uv sync
```

Configure:

```bash
cp .env.example .env
```

Apply schema:

```bash
uv run alembic upgrade head
```

Start:

```bash
uv run uvicorn app.main:app --reload
```

Endpoints:

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Liveness: http://localhost:8000/health
- Readiness: http://localhost:8000/ready

## Authentication and authorization

Authentication uses bearer JWTs. Every authenticated request checks both:

1. the JWT signature and claims, and
2. the current active user row in PostgreSQL.

A previously issued token therefore cannot keep working after the account is deactivated.

### Roles

| Capability | Viewer | Operator | Admin |
|---|:---:|:---:|:---:|
| Read telemetry | ✓ | ✓ | ✓ |
| Read alerts | ✓ | ✓ | ✓ |
| Acknowledge alerts/incidents |  | ✓ | ✓ |
| Simulation controls |  | ✓ | ✓ |
| Read hosts | ✓ | ✓ | ✓ |
| Manage hosts |  |  | ✓ |
| Manage API keys |  |  | ✓ |
| Manage alert rules |  |  | ✓ |
| Manage SLOs |  |  | ✓ |
| Manage users |  |  | ✓ |
| View audit/operational metrics |  |  | ✓ |

Public signup creates an active viewer account. Admins can promote accounts through the existing user management API.

## REST API

### Public and authentication

| Method | Endpoint | Access |
|---|---|---|
| GET | /health | Public |
| GET | /ready | Public |
| POST | /api/auth/login | Public |
| POST | /api/auth/signup | Public |
| GET | /api/auth/me | Authenticated |
| POST | /api/auth/change-password | Authenticated |

### Users and hosts

| Method | Endpoint | Access |
|---|---|---|
| GET | /api/users | Admin |
| POST | /api/users | Admin |
| PATCH | /api/users/{user_id} | Admin |
| GET | /api/hosts | Authenticated |
| POST | /api/hosts | Admin |
| PATCH | /api/hosts/{host_id} | Admin |
| DELETE | /api/hosts/{host_id} | Admin |

The backend protects the last active administrator and prevents an administrator from accidentally removing their own admin access.

### Telemetry and streaming

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| GET | /api/telemetry/current | Authenticated | Current event |
| GET | /api/telemetry/history | Authenticated | Time/host bounded history |
| GET | /api/telemetry/stats | Authenticated | Database-side aggregate statistics |
| POST | /api/ingest/v1/telemetry | Agent key | Host telemetry ingestion |
| WS | /ws/telemetry?token=<jwt> | Authenticated | Live telemetry/alerts/system events |

History and statistics accept host/time-window filtering. Queries use a composite `(host_id, timestamp)` index.

### Alerts, rules, and incidents

| Method | Endpoint | Access |
|---|---|---|
| GET | /api/alerts | Authenticated |
| POST | /api/alerts/{alert_id}/acknowledge | Operator/Admin |
| GET | /api/alert-rules | Admin |
| POST | /api/alert-rules | Admin |
| PATCH | /api/alert-rules/{rule_id} | Admin |
| DELETE | /api/alert-rules/{rule_id} | Admin |
| GET | /api/incidents | Authenticated |
| GET | /api/incidents/{incident_id}/evidence | Authenticated |
| GET | /api/incidents/{incident_id} | Authenticated |
| POST | /api/incidents/{incident_id}/acknowledge | Operator/Admin |

### Change events and incident evidence

| Method | Endpoint | Access |
|---|---|---|
| GET | /api/changes | Authenticated |
| POST | /api/changes | Operator/Admin |
| GET | /api/incidents/{incident_id}/evidence | Authenticated |

Change events represent deployments, configuration changes, feature-flag changes, maintenance, and rollbacks. They can be emitted manually or from CI/CD with an external build or release reference.

Incident evidence is deterministic: it joins the incident's recorded alerts with nearby change events in a ±30 minute correlation window and returns a chronological timeline plus explicit findings. The backend does not infer a root cause beyond the evidence it can actually correlate.

Example CI/CD integration:

\`\`\`bash
curl -X POST "$TELEMETRY_API_URL/api/changes" \\
  -H "Authorization: Bearer $TELEMETRY_OPERATOR_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"event_type":"deployment","title":"Release 2026.09.22","source":"github-actions","external_ref":"run-8421","host_id":"<host-id>"}'
\`\`\`

### Agent credentials

| Method | Endpoint | Access |
|---|---|---|
| GET | /api/api-keys | Admin |
| POST | /api/api-keys/hosts/{host_id} | Admin |
| POST | /api/api-keys/{key_id}/revoke | Admin |

The plaintext secret is returned only at creation time. PostgreSQL stores only a SHA-256 digest and a short display prefix.

### SLOs

| Method | Endpoint | Access |
|---|---|---|
| GET | /api/slos | Authenticated |
| GET | /api/slos/{slo_id}/status | Authenticated |
| POST | /api/slos | Admin |
| PATCH | /api/slos/{slo_id} | Admin |
| DELETE | /api/slos/{slo_id} | Admin |

The SLO status endpoint returns total/good/bad samples, SLI percentage, objective, error budget, remaining budget, and compliance.

### Simulation

| Method | Endpoint | Access |
|---|---|---|
| GET | /api/simulation/status | Authenticated |
| POST | /api/simulation/start | Operator/Admin |
| POST | /api/simulation/pause | Operator/Admin |
| POST | /api/simulation/resume | Operator/Admin |
| POST | /api/simulation/reset | Operator/Admin |
| POST | /api/simulation/rate?rate=N | Operator/Admin |
| POST | /api/simulation/trigger | Operator/Admin |

The simulator remains valuable as a deterministic fault-injection harness for validating alerting and incident workflows without external infrastructure.

### Internal observability

| Method | Endpoint | Access |
|---|---|---|
| GET | /api/observability/metrics | Admin |
| GET | /api/observability/metrics/prometheus | Admin |
| GET | /api/observability/audit-logs | Admin |

### Notifications

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| GET | /api/notification-channels | Admin | List configured webhook channels |
| POST | /api/notification-channels | Admin | Create a webhook channel |
| PATCH | /api/notification-channels/{channel_id} | Admin | Update routing/security settings |
| DELETE | /api/notification-channels/{channel_id} | Admin | Remove a channel |
| GET | /api/notification-channels/deliveries | Admin | Inspect delivery state and retry history |

Notifications use a durable, bounded delivery pipeline rather than sending network requests from the telemetry hot path.

A channel can independently receive alerts and/or incidents and can filter by minimum severity. Delivery records are deduplicated by channel + event type + event ID.

The dispatcher provides:

- bounded in-process queueing
- durable delivery state in PostgreSQL
- restart recovery for pending work
- bounded exponential backoff
- retry handling for network failures, HTTP 429, and HTTP 5xx
- permanent failure handling for non-retryable HTTP 4xx responses
- delivery attempt/error timestamps for troubleshooting
- audit logging for channel administration
- webhook URL masking in API responses
- HTTPS-only webhook destinations with private/reserved address rejection

No paid notification platform is required. Any HTTPS endpoint that accepts JSON can be used for local testing or self-hosted automation.

Example payloads use a versioned envelope such as:

```json
{
  "version": 1,
  "event": "alert.firing",
  "alert": {
    "id": "…",
    "host_id": "…",
    "metric": "cpu",
    "severity": "CRITICAL",
    "message": "CPU threshold exceeded"
  }
}
```

The notification worker is intentionally isolated from telemetry generation. A slow or unavailable webhook can increase delivery backlog without blocking live telemetry processing.

## Real host telemetry agent

The repository includes a free, open-source host agent under `agent/`.

It collects:

- CPU utilization
- memory utilization
- network throughput
- available temperature sensors
- optional HTTP probe latency and success/error signals

The agent sends bounded batches to:

```text
POST /api/ingest/v1/telemetry
X-Telemetry-Key: tlm_<secret>
```

### Windows / PowerShell example

Create a host and an API key in the admin UI/API, then:

```powershell
$env:TELEMETRY_API_URL="http://localhost:8000"
$env:TELEMETRY_API_KEY="tlm_<secret>"
$env:TELEMETRY_AGENT_INTERVAL="2"
$env:TELEMETRY_AGENT_BATCH_SIZE="10"

uv run python -m agent.telemetry_agent --check
uv run python -m agent.telemetry_agent
```

The agent retries transient network/5xx failures with bounded exponential backoff, honors `Retry-After` for 429 responses, and refuses to retry a rejected credential.

## Ingestion reliability

The ingestion contract is intentionally designed around at-least-once delivery:

1. The agent batches samples.
2. A batch can be retried after a transient failure.
3. Events are deduplicated by `(host_id, sequence)`.
4. PostgreSQL enforces uniqueness as the final concurrency guard.
5. Only successfully inserted events enter the live processing pipeline.
6. Host `last_seen_at` and agent version are updated from accepted events.

This makes retries safe without requiring Redis or a paid queueing service.

## Alerting model

There are two complementary detectors.

### Anomaly detector

A rolling z-score detects statistical deviation from a host-specific baseline:

```text
z = (value - rolling_mean) / rolling_standard_deviation
```

Detection waits for enough history and maintains state per host and metric.

### Rule engine

Administrators can define deterministic rules such as:

```text
cpu >= 80 for 30 seconds
latency_ms > 250
error_rate >= 5
memory > 90
```

Rules support:

- comparison operators
- sustained duration
- cooldown
- per-host state
- enable/disable
- deduplicated active alerts
- explicit resolution

This gives the platform both statistical detection and operator-defined policy.

## Incident correlation

The incident engine groups alert transitions for the same host inside a short correlation window.

An incident tracks:

- first/last seen time
- severity escalation
- associated alert IDs
- active alert count
- acknowledgement state
- resolution time

A newly arriving signal reopens an acknowledged incident so a fresh failure cannot remain hidden behind an old acknowledgement. Close-but-out-of-order events are also supported.

Incident persistence is asynchronous and retries until the referenced alert is durable.

## SLO and error budget

SLOs evaluate persisted telemetry in PostgreSQL rather than loading the full dataset into Python.

For each configured host and window the backend calculates:

- total samples
- good samples
- bad samples
- sample-based SLI
- target objective
- total error budget
- remaining error budget
- compliance state

The implementation is deliberately explicit that the SLI is **sample-based**; it is not presented as a time-weighted availability calculation.

## Self-observability

The backend observes itself with dependency-free process metrics.

Examples include:

- generated telemetry count
- ingested telemetry count
- ingestion rejections
- persistence drops
- alert creation/resolution
- incident creation/resolution
- rule evaluations/fires/resolutions
- WebSocket connections
- current WebSocket clients
- persistence queue depth
- open incidents
- HTTP request volume
- HTTP 4xx/5xx counts
- HTTP request duration sum/count

`/api/observability/metrics/prometheus` emits Prometheus text format, so a local Prometheus/Grafana setup can consume the data without a paid service.

## Security and abuse protection

Security controls are intentionally built into the backend rather than left to deployment folklore:

- Argon2 password hashing
- JWT bearer authentication
- active-user lookup on every authenticated request
- role-based authorization
- hashed machine credentials
- revocable host-scoped API keys
- public signup protection
- login protection
- ingestion protection
- audit events for sensitive administration/operations
- production startup rejection of known development secrets
- no trust in client-controlled `X-Forwarded-For` for rate-limit identity

The rate limiter is process-local by design. In a horizontally scaled deployment it would need shared state.

## Persistence and performance

Telemetry and alert writes are handled by bounded background workers.

The hot path is:

```text
collect → validate → update memory → detect → rule-evaluate → correlate → broadcast
```

Database writes happen asynchronously.

Queries use bounded limits and database-side aggregation. Telemetry filtering is indexed by host and timestamp.

This keeps the architecture honest: it improves the single-process deployment without pretending that local memory is a distributed stream.

## Database migrations

Apply:

```bash
uv run alembic upgrade head
```

Inspect:

```bash
uv run alembic current
```

Create a migration after model changes:

```bash
uv run alembic revision --autogenerate -m "describe the change"
```

Review autogenerated migrations before committing them.

The current migration chain is:

```text
0001_initial_schema
        ↓
0002_observability_features
        ↓
0003_ingestion_idempotency
        ↓
0004_slos
        ↓
0005_telemetry_query_index
        ↓
0006_telemetry_storage_hardening
        ↓
0007_change_events
        ↓
0008_notification_pipeline
```

## Testing strategy

Run:

```bash
uv run pytest -q
```

Coverage includes:

- authentication and signup
- authorization and role boundaries
- production configuration validation
- rate limiter behavior
- database model registration
- telemetry generation
- host-isolated anomaly detection
- alert rule state machines
- idempotent ingestion
- API key creation/revocation
- incident correlation/reopening/resolution
- incident persistence rehydration
- SLO/error-budget evaluation
- request RED metrics
- telemetry manager lifecycle
- persistence behavior
- REST route registration
- simulation APIs
- WebSocket behavior
- notification routing and webhook URL security
- notification delivery idempotency
- retry/backoff scheduling on webhook failures
- integration flows from authentication through telemetry/alerts

GitHub Actions starts PostgreSQL, applies Alembic migrations, runs the full suite, and verifies the final migration state.

## Docker

Build:

```bash
docker build -t telemetry-backend .
```

Run:

```bash
docker run --rm -p 8000:8000 --env-file .env telemetry-backend
```

The image runs as a non-root user. Apply database migrations separately before starting the application.

## Scaling boundary

The current service intentionally uses a single process for real-time ownership.

That gives clear guarantees:

- one process owns live telemetry state
- one process owns the in-memory rule/anomaly state
- WebSocket subscribers receive the same process-local stream
- PostgreSQL stores durable history
- rate limiting and runtime metrics are process-local

A future multi-worker deployment would require shared coordination for at least rate limiting, alert/rule state, and WebSocket fan-out. PostgreSQL LISTEN/NOTIFY or another shared transport could be introduced only when the deployment actually needs it.

That boundary is intentional: the project demonstrates the difference between making a single-process system robust and falsely calling it horizontally scalable.

## License

MIT
