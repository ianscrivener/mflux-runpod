"""Sync mflux-runner-docker-test's ORCHESTRATOR_BASE_URL to match the
current mflux-orchestrator deployment.

Run this after every orchestrator deploy (see .github/workflows/deploy.yml)
-- the orchestrator's URL changes on every redeploy (Flash always creates a
new endpoint id, never updates in place), but the Docker Runner's
ORCHESTRATOR_BASE_URL is a plain env var on a manually-managed endpoint
that doesn't auto-track it. Confirmed live, 2026-08-18: a stale value here
makes every Runner->Orchestrator callback fail with a DNS resolution
error, silently stranding run records at status "running" forever (the
Runner reports callback_delivered=false, but nothing surfaces that unless
someone's actually watching the job output).

Usage: RUNPOD_API_KEY=... python scripts/sync_runner_orchestrator_url.py
"""

import os
import sys

import httpx

from resolve_orchestrator_url import resolve_base_url

RUNPOD_API_BASE = "https://api.runpod.io/v2"
RUNNER_ENDPOINT_NAME = "mflux-runner-docker-test"


def _find_runner_endpoint(api_key: str) -> dict:
    resp = httpx.get(
        f"{RUNPOD_API_BASE}/serverless",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    endpoints = resp.json()
    items = endpoints if isinstance(endpoints, list) else endpoints.get("endpoints", [])
    for ep in items:
        if ep.get("name") == RUNNER_ENDPOINT_NAME:
            return ep
    raise SystemExit(f"No deployed endpoint named {RUNNER_ENDPOINT_NAME!r} found")


def main() -> None:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("RUNPOD_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    orchestrator_url = resolve_base_url(api_key)
    runner = _find_runner_endpoint(api_key)
    current_env = runner.get("env", {})

    if current_env.get("ORCHESTRATOR_BASE_URL") == orchestrator_url:
        print(f"Already in sync: {orchestrator_url}")
        return

    # env is a full-replace field on PATCH, not a merge -- send every
    # existing key back, only changing ORCHESTRATOR_BASE_URL.
    new_env = {**current_env, "ORCHESTRATOR_BASE_URL": orchestrator_url}
    resp = httpx.patch(
        f"{RUNPOD_API_BASE}/serverless/{runner['id']}",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"env": new_env},
        timeout=30.0,
    )
    resp.raise_for_status()
    print(f"Synced {RUNNER_ENDPOINT_NAME}'s ORCHESTRATOR_BASE_URL -> {orchestrator_url}")


if __name__ == "__main__":
    main()
