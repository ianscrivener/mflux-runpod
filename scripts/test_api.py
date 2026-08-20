"""Live API smoke test -- hits the real, running local Orchestrator and
prints a one-line summary per endpoint. Not a pytest suite (tests/ covers
that with mocks, no live calls) -- this is `just test-api`, a quick "is it
actually up and returning sane data" check against the real thing.

Usage:
  API_BASE_URL=http://127.0.0.1:8000 python scripts/test_api.py

Exits non-zero if any endpoint fails, so it's usable as a CI/deploy gate.
"""

import os
import sys

import httpx


def main() -> None:
    base_url = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    print(f"Target: {base_url}\n")

    ok = True

    def check(name: str, method: str, path: str, summarize) -> None:
        nonlocal ok
        try:
            resp = httpx.request(method, f"{base_url}{path}", timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
            print(f"  [ok]   {name:<16} {summarize(data)}")
        except Exception as exc:  # noqa: BLE001 - report every failure, don't stop the run
            ok = False
            print(f"  [FAIL] {name:<16} {exc}")

    check("health", "GET", "/health", lambda d: d.get("status", d))
    check(
        "models_mflux", "GET", "/models_mflux",
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
