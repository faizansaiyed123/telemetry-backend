# Live Real Telemetry Implementation Log

## Task
Make the application consume genuine live telemetry from a real machine and surface it through the existing backend/WebSocket/frontend pipeline, using free/open-source components only.

## Safety boundary
- Existing `main` and existing feature branches were not modified.
- Dedicated branch: `feat/live-real-telemetry-backend`
- Branch base: `main` at `9807a03c9c0573784b36ac560a777e53f3d68c6b`
- Backend PR: #18 (open, not merged)

## Architecture inspection
The current backend already contained a real host agent using `psutil`, host-scoped API keys, authenticated `POST /api/ingest/v1/telemetry`, batched delivery, retry/backoff, idempotent persistence by `(host_id, sequence)`, host `last_seen_at`, and a shared `TelemetryManager.process_event()` pipeline. The frontend already consumed authenticated WebSocket telemetry and represented the telemetry source and host metadata in its data types.

Relevant existing ahead branches were inspected. They remain untouched. The most substantial backend branch, `feat/backend-synthetic-monitoring-topology`, is divergent from current main and was not reused. Existing frontend observability branches were also compared against current main and were not reused.

## Research decisions
- `psutil` is appropriate for a zero-cost cross-platform host agent.
- OpenTelemetry documents host/resource telemetry and collector/receiver patterns; the implementation keeps the existing lightweight Python agent rather than adding an unnecessary external collector.
- OWASP guidance supports host-scoped API credentials, rate limiting, and avoiding plaintext secret storage.
- The project explicitly avoids paid monitoring SaaS, Redis, and paid infrastructure.

## Implemented on this branch
1. Added `TELEMETRY_SOURCE_MODE` with supported values:
   - `synthetic`
   - `hybrid`
   - `agent`
2. Passed source mode into `TelemetryManager`.
3. Added source-aware health state:
   - `source_mode`
   - `simulation_enabled`
   - `events_ingested`
   - agent mode reports the telemetry pipeline as active without pretending a simulator is running.
4. Made agent-only runtime avoid binding live ingestion to the synthetic persistence host.
   - Alert/incident persistence still initializes for real incoming telemetry.
   - Real agent events continue through anomaly/rule/incident/WebSocket processing.
5. Made simulation APIs reject in agent mode with `409 Conflict`, preventing accidental synthetic contamination of a real-data deployment.
6. Added source-aware fields to simulation status.
7. Updated `.env.example` and README with the real-only mode.
8. Added regression tests for supported source modes, invalid modes, agent-mode startup behavior, and simulation blocking.

## Important integrity decision
The real agent remains the actual data source. No fake values were introduced to make the live UI appear populated.

The existing optional HTTP probe measures request/response latency and success state. It is not presented as real application traffic RPS; the code/documentation must not claim that a single probe represents application request volume.

## Verification
- Local repository clone/test execution was attempted but the execution environment cannot resolve github.com, so no local test run is being claimed.
- GitHub Actions PR run #477 was triggered for this branch.
- Current recorded CI state at log update: dependencies and Alembic verification completed; full test step still in progress.
- No merge to main has been performed.

## Resume checkpoint
Next action after CI completion:
1. Inspect run #477 result and exact logs.
2. Fix any failures if present and re-run until backend CI passes.
3. Only after backend verification, create a separate frontend branch from current `main` for the live-data UI integration.
4. Frontend must prefer/identify real agent telemetry, expose source/host/liveness clearly, and never silently substitute synthetic data.
