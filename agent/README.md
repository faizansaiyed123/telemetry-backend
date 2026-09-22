# Telemetry Host Agent

A zero-cost, open-source host collector that sends real machine telemetry to the Telemetry platform.

## What it collects

The agent uses psutil to collect CPU utilization, memory utilization, network throughput, and available host temperature sensors.

An optional HTTP probe measures request latency and success/error state for a URL you control. Those probe signals are service-level signals, not a claim about application traffic volume.

## Setup

From the repository root:

~~~powershell
uv sync
~~~

Configure the host-scoped API key created by an administrator:

~~~powershell
$env:TELEMETRY_API_URL="http://localhost:8000"
$env:TELEMETRY_API_KEY="tlm_<secret>"
$env:TELEMETRY_AGENT_INTERVAL="2"
$env:TELEMETRY_AGENT_BATCH_SIZE="10"
~~~

Run a single collection check:

~~~powershell
uv run python -m agent.telemetry_agent --check
~~~

Run continuously:

~~~powershell
uv run python -m agent.telemetry_agent
~~~

For a local development server using a self-signed certificate, TLS verification can be disabled explicitly with TELEMETRY_VERIFY_TLS=false. Keep verification enabled for real deployments.

## Reliability behavior

The agent batches events, retries transient delivery failures with bounded exponential backoff, respects server Retry-After responses, and stops retrying when the host credential is rejected.

The backend authenticates each agent with a host-scoped API key, stores only a SHA-256 hash of the secret, deduplicates telemetry by (host_id, sequence), and records the host last-seen timestamp.

## Environment variables

| Variable | Default | Purpose |
|---|---:|---|
| TELEMETRY_API_URL | — | Backend base URL; required |
| TELEMETRY_API_KEY | — | Host-scoped credential; required |
| TELEMETRY_AGENT_INTERVAL | 2 | Collection interval in seconds |
| TELEMETRY_AGENT_BATCH_SIZE | 10 | Maximum events per request |
| TELEMETRY_AGENT_VERSION | telemetry-agent/1.0 | Agent identity |
| TELEMETRY_PROBE_URL | — | Optional URL to probe |
| TELEMETRY_VERIFY_TLS | true | Verify HTTPS certificates |
| TELEMETRY_AGENT_TIMEOUT | 10 | HTTP delivery timeout in seconds |

The agent requires no paid SaaS, cloud infrastructure, Redis, or message broker.