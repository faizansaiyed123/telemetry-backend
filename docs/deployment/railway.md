# Telemetry Railway deployment

Deploy PostgreSQL as a managed Railway database and this repository as the API service.

- Start command: `sh -c 'exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT'`.
- Readiness: `/ready`.
- Required production variables include `DATABASE_URL`, `JWT_SECRET_KEY`, `BOOTSTRAP_ADMIN_PASSWORD`, `CORS_ALLOWED_ORIGINS`, and `WEBHOOK_SIGNING_SECRET` when webhooks are enabled.
- Keep `TELEMETRY_SOURCE_MODE=synthetic` or `hybrid` for the synthetic recruiter experience.
- Do not publish the bootstrap admin credentials into the portfolio or frontend.

The recruiter demo is not considered live from this hosting configuration alone. A separate temporary guest/namespace flow still has to be implemented and verified so one visitor cannot control another visitor's telemetry stream.
