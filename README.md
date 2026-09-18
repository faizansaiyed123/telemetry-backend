# Telemetry Backend

FastAPI backend for a real-time telemetry monitoring platform.

## Included

- PostgreSQL persistence with SQLAlchemy and Alembic
- JWT authentication with Argon2 password hashing
- Admin, operator, and viewer roles
- User and host management APIs
- Persistent telemetry and alert history
- Authenticated WebSocket telemetry streaming
- Simulation controls and deterministic anomaly detection
- Bounded async persistence workers
- Liveness and readiness probes
- Production configuration validation
- Non-root Docker runtime with healthcheck

Redis is not required by the current single-process architecture.

## Setup

Requirements: Python 3.13, uv, and PostgreSQL 16+.

~~~bash
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
~~~

Swagger UI: http://localhost:8000/docs

## Testing

~~~bash
uv run pytest -q
~~~

CI runs the suite against PostgreSQL after applying Alembic migrations.

## Production

Set APP_ENV=production and replace the development JWT secret and bootstrap password. Production startup rejects the known placeholders and requires a JWT secret of at least 32 characters and a bootstrap password of at least 12 characters.

## Health

- GET /health — liveness
- GET /ready — database/application readiness

## Authentication

- POST /api/auth/login — issue JWT
- GET /api/auth/me — current user
- POST /api/auth/change-password — change the authenticated user's password

## Management

- GET/POST/PATCH /api/users... — administrator user management
- GET/POST/PATCH/DELETE /api/hosts... — host management

## Telemetry

- GET /api/telemetry/current
- GET /api/telemetry/history
- GET /api/telemetry/stats
- GET /api/alerts
- POST /api/alerts/{id}/acknowledge

## Simulation

- GET /api/simulation/status
- POST /api/simulation/start
- POST /api/simulation/pause
- POST /api/simulation/resume
- POST /api/simulation/reset
- POST /api/simulation/rate
- POST /api/simulation/trigger

## WebSocket

ws://localhost:8000/ws/telemetry?token=<jwt>

## Docker

The image excludes test sources, runs as a non-root user, and includes a liveness healthcheck.

~~~bash
docker build -t telemetry-backend .
docker run --rm -p 8000:8000 --env-file .env telemetry-backend
~~~

Run alembic upgrade head against the configured database before starting the application.