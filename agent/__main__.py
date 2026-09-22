"""Command-line entrypoint for the zero-cost telemetry agent."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from .client import TelemetryAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ship host telemetry to Telemetry Platform")
    parser.add_argument("--base-url", default=os.getenv("TELEMETRY_BASE_URL"))
    parser.add_argument("--api-key", default=os.getenv("TELEMETRY_API_KEY"))
    parser.add_argument("--agent-version", default="1.0.0")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--requests-per-second", type=float, default=0.0)
    parser.add_argument("--error-rate", type=float, default=0.0)
    parser.add_argument("--latency-ms", type=float, default=0.0)
    parser.add_argument(
        "--temperature-fallback",
        type=float,
        default=0.0,
        help="Fallback when the OS exposes no temperature sensor (0 means unavailable).",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


async def _run(args: argparse.Namespace) -> None:
    if not args.base_url:
        raise SystemExit("--base-url or TELEMETRY_BASE_URL is required")
    if not args.api_key:
        raise SystemExit("--api-key or TELEMETRY_API_KEY is required")

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop_event = asyncio.Event()
    agent = TelemetryAgent(
        base_url=args.base_url,
        api_key=args.api_key,
        agent_version=args.agent_version,
        interval_seconds=args.interval,
        batch_size=args.batch_size,
        requests_per_second=args.requests_per_second,
        error_rate=args.error_rate,
        latency_ms=args.latency_ms,
        temperature_fallback=args.temperature_fallback,
    )
    try:
        await agent.run(stop_event)
    except KeyboardInterrupt:
        stop_event.set()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
