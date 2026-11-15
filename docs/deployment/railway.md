# Telemetry Railway deployment

Deploy this repository as a single FastAPI service and connect it to a dedicated managed PostgreSQL database.

## Normal application service

- Start command: `sh -c 'exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT'`.
- Liveness: `/health`.
- Readiness: `/ready`.
- Required production variables include `DATABASE_URL`, `JWT_SECRET_KEY`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, and `CORS_ALLOWED_ORIGINS`.
- Use a dedicated PostgreSQL database. Never connect a public demo deployment to production/customer data.
- Keep the deployment's bootstrap administrator credentials out of the portfolio and frontend.

## Isolated recruiter demo environment

The anonymous demo is a feature-flagged capability. It is not active unless the dedicated demo deployment explicitly sets:

- `DEMO_ENABLED=true`
- `DEMO_SESSION_TTL_MINUTES=120`
- `DEMO_MAX_ACTIVE_SESSIONS_PER_IP=2`
- `DEMO_BOOTSTRAP_REQUESTS_PER_IP=4`
- `TRUST_RAILWAY_X_REAL_IP=true` only when all public API traffic enters through Railway's public HTTP edge.

The demo service must use its own database and must not contain production/customer records. The application writes a disposable demo identity row and an HMAC hash of the source IP; raw client IP addresses are not stored in that row. Synthetic telemetry, alerts, incidents, anomaly state and WebSocket connections live in the demo identity's in-memory runtime, not in shared telemetry tables. Ending a session stops its runtime and deletes its identity row. Expired sessions are stopped and deleted by the background cleanup loop (30-second sweep interval).

### Single-instance requirement

The session registry, per-IP rate limiter, telemetry runtime and WebSocket connections are process-local. Run exactly one API instance for this version of the demo. Do not enable horizontal autoscaling or multiple replicas: a request routed to another process would not find the session's in-memory runtime. A process restart invalidates all active demo sessions. Multi-instance deployment requires moving session/runtime ownership to a shared coordination layer first.

### Public-edge client IP trust

Railway's public HTTP edge replaces `X-Real-IP` with the observed client address. The application validates that value only when `TRUST_RAILWAY_X_REAL_IP=true`; otherwise it uses the direct socket peer. Enable this setting only when the service is exposed through Railway public HTTP ingress and untrusted callers cannot reach the API through Railway private networking or a TCP proxy. Those alternate paths bypass public-edge header sanitization. Never rely on arbitrary `X-Forwarded-For` values for the demo session quota.

### External side effects and persistence

For the recruiter-only deployment set:

- `WEBHOOK_NOTIFICATIONS_ENABLED=false`
- `DISTRIBUTED_EVENT_FANOUT_ENABLED=false`
- `TELEMETRY_PERSISTENCE_ENABLED=false`

Demo-specific telemetry managers independently disable persistence, webhook dispatch and distributed event fan-out even if the general application settings are enabled. Disabling the deployment-wide webhook dispatcher is an additional safeguard for the non-demo global runtime.

Keep `JWT_SECRET_KEY` strong and unique for this environment, and keep `BOOTSTRAP_ADMIN_PASSWORD` strong and private. Do not expose the admin account to recruiters; the demo uses randomly created viewer identities without signup.

### What the recruiter can currently use

The isolated demo runtime exposes its own:

- live synthetic telemetry stream and WebSocket,
- telemetry history, statistics and chart series,
- simulation start/pause/resume/reset, rate and anomaly controls,
- alerts and acknowledgements,
- incidents and acknowledgements.

Demo identities are denied access to shared host inventory, change-event records, SLOs, service topology, synthetic-check configuration/history and database-backed incident evidence. Those capabilities must not be shown as available in the recruiter demo until they are backed by per-session/tenant-owned data.

The feature flag must remain `DEMO_ENABLED=false` on any normal/customer deployment. A successful Railway build or a working `/ready` endpoint alone is not proof that the recruiter workflow is verified; test session creation, two-session isolation, WebSocket routing, reset, revoke and expiry against the deployed service before publishing a demo URL.
