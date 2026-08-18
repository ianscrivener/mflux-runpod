# List available recipes
default:
    @just --list

# Run the fast unit test suite (mocked, no live network calls)
test:
    .venv/bin/pytest tests/ -q

# Hit the live Orchestrator's endpoints and print a one-line summary each
test-api:
    .venv/bin/python3 scripts/test_api.py

# Fetch one Orchestrator endpoint as raw JSON. Resolves the current
# mflux-orchestrator URL fresh each call (via scripts/resolve_orchestrator_url.py
# -- it changes on every redeploy) and echoes the literal curl command to
# stderr first, so `just health` etc. show exactly what's being sent.
_fetch path:
    #!/usr/bin/env bash
    set -euo pipefail
    url=$(.venv/bin/python3 scripts/resolve_orchestrator_url.py)
    echo "curl -H 'Authorization: Bearer \$RUNPOD_API_KEY' ${url}{{ path }}" >&2
    curl -s -H "Authorization: Bearer $RUNPOD_API_KEY" "${url}{{ path }}" | .venv/bin/python3 -m json.tool

# GET /health, raw JSON
health: (_fetch "/health")

# GET /models_supported, raw JSON
models_supported: (_fetch "/models_supported")

# GET /models_hf, raw JSON
models_hf: (_fetch "/models_hf")

# GET /models_missing, raw JSON
models_missing: (_fetch "/models_missing")

# GET /model_store, raw JSON
model_store: (_fetch "/model_store")

# GET /report, raw JSON
report: (_fetch "/report")
