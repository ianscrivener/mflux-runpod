"""Live API smoke test -- hits the real, running Orchestrator (deployed on
RunPod by default, or a local one via API_BASE_URL) and prints a one-line
summary per endpoint. Not a pytest suite (tests/ covers that with mocks,
no live calls) -- this is `just test-api`, a quick "is it actually up and
returning sane data" check against the real thing.

Individual endpoints (`just health`, `just models_hf`, ...) are plain curl
commands in the justfile instead -- see scripts/resolve_orchestrator_url.py,
which this also uses, for why the URL isn't hardcoded here either.

Usage:
  RUNPOD_API_KEY=... python scripts/test_api.py
  API_BASE_URL=http://127.0.0.1:8791 python scripts/test_api.py   # local dev server

Exits non-zero if any endpoint fails, so it's usable as a CI/deploy gate.
"""

import os
import sys

import httpx

from resolve_orchestrator_url import resolve_base_url


def _resolve_base_url(api_key: str) -> str:
    explicit = os.environ.get("API_BASE_URL")
    return explicit.rstrip("/") if explicit else resolve_base_url(api_key)


def main() -> None:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise SystemExit("RUNPOD_API_KEY not set")

    base_url = _resolve_base_url(api_key)
    print(f"Target: {base_url}\n")

    # RunPod authenticates LB endpoints at the edge -- every path 401s
    # without this, including /health. Harmless no-op against a local
    # plain-FastAPI dev server, which doesn't check it.
    headers = {"Authorization": f"Bearer {api_key}"}

    ok = True

    def check(name: str, method: str, path: str, summarize) -> None:
        nonlocal ok
        try:
            resp = httpx.request(method, f"{base_url}{path}", headers=headers, timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            print(f"  [ok]   {name:<16} {summarize(data)}")
        except Exception as exc:  # noqa: BLE001 - report every failure, don't stop the run
            ok = False
            print(f"  [FAIL] {name:<16} {exc}")

    check("health", "GET", "/health", lambda d: d.get("status", d))
    check(
        "models_supported", "GET", "/models_supported",
        lambda d: f"{len(d)} supported model configs",
    )
    check(
        "models_hf", "GET", "/models_hf",
        lambda d: f"{len(d.get('hf_models', []))} models published on Hugging Face",
    )
    check(
        "models_missing", "GET", "/models_missing",
        lambda d: f"{len(d.get('missing', []))} series missing, "
        f"{len(d.get('complete', []))} complete",
    )
    check(
        "model_store", "GET", "/model_store",
        lambda d: f"{len(d.get('volumes', []))} active ephemeral build volume(s)",
    )
    check(
        "report", "GET", "/report",
        lambda d: f"{len(d.get('runs', []))} recent run(s)",
    )

    print()
    if not ok:
        print("Some endpoints failed.")
        sys.exit(1)
    print("All endpoints OK.")


if __name__ == "__main__":
    main()
