"""Print the deployed mflux-orchestrator endpoint's base URL, and nothing
else, to stdout. One job: resolve by name via RunPod's REST API rather than
a hardcoded URL, since the endpoint id/URL changes on every redeploy.

Used by justfile as a variable, e.g.:
  orchestrator_url := `.venv/bin/python3 scripts/resolve_orchestrator_url.py`
so every `just <endpoint>` recipe can build a plain, visible curl command
against a URL that's always current -- run `just --show health` (or just
watch `just health`'s own echoed command) to see exactly what gets sent.

Usage: RUNPOD_API_KEY=... python scripts/resolve_orchestrator_url.py
"""

import os
import sys
import time

import httpx

RUNPOD_API_BASE = "https://rest.runpod.io/v1"
ENDPOINT_NAME = "mflux-orchestrator"

# deploy.yml's clean-slate step deletes the old endpoint before `flash
# deploy` creates the new one, so there's a real ~1min window with no
# mflux-orchestrator at all. Retry through it instead of failing on the
# first `just <endpoint>` call that happens to land in that gap.
_RETRY_ATTEMPTS = 6
_RETRY_DELAY_S = 5


def _find(api_key: str) -> str | None:
    resp = httpx.get(
        f"{RUNPOD_API_BASE}/endpoints",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    endpoints = resp.json()
    for ep in endpoints if isinstance(endpoints, list) else endpoints.get("items", []):
        if ep.get("name") == ENDPOINT_NAME:
            base = ep.get("requestUrls", {}).get("base") or f"https://{ep['id']}.api.runpod.ai"
            return base.rstrip("/")
    return None


def resolve_base_url(api_key: str) -> str:
    for attempt in range(_RETRY_ATTEMPTS):
        base = _find(api_key)
        if base:
            return base
        if attempt < _RETRY_ATTEMPTS - 1:
            print(
                f"{ENDPOINT_NAME!r} not found (attempt {attempt + 1}/{_RETRY_ATTEMPTS}) "
                f"-- retrying in {_RETRY_DELAY_S}s (likely mid-redeploy)",
                file=sys.stderr,
            )
            time.sleep(_RETRY_DELAY_S)
    raise SystemExit(f"No deployed endpoint named {ENDPOINT_NAME!r} found")


if __name__ == "__main__":
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("RUNPOD_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    print(resolve_base_url(api_key))
