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

# Open the current Orchestrator's /docs in your browser (will likely 401, browser can't send the Bearer header)
open:
    #!/usr/bin/env bash
    set -euo pipefail
    url=$(.venv/bin/python3 scripts/resolve_orchestrator_url.py)
    echo "Opening ${url}/docs (will 401 without a Bearer header -- browser can't send one)" >&2
    open "${url}/docs"

# Sync the Orchestrator's code+config to its standalone local runtime folder
# (/Users/ianscrivener/bin/MFlux_Orchestrator, run there via `uv run uvicorn app.main:app`).
# Explicit file allowlist, not a wildcard copy of app/ -- so Runner/Flash-only
# files (runner.py, orchestrator_endpoint.py, runner_endpoint.py) never leak in,
# even if someone adds more app/*.py later without updating this. Never touches
# data/reports.sqlite, data/models_hf.json, or .env at the destination -- those
# are that deployment's own live runtime state/secrets, not source to be
# overwritten on a sync. orchestrator-local/{justfile,README.md,pyproject.toml}
# are the hand-authored deployment files (svc-add/svc-del, docs, trimmed deps)
# -- edit them here, not in the destination, so they stay version-controlled.
update-orchestrator:
    #!/usr/bin/env bash
    set -euo pipefail
    dest=/Users/ianscrivener/bin/MFlux_Orchestrator
    mkdir -p "$dest/app" "$dest/configs/models" "$dest/data" "$dest/data-hf-sync"
    cp app/__init__.py app/main.py app/db.py app/generate.py app/models_hf.py \
       app/models_missing.py app/models_supported.py app/outbox.py app/report.py \
       app/runpod_volumes.py app/series_lifecycle.py app/hf_datasets.py \
       app/runpod_skus.py app/queue_store.py "$dest/app/"
    cp configs/models/*.yaml "$dest/configs/models/"
    cp configs/overrides.yaml configs/hf_datasets.yaml "$dest/configs/"
    cp data-hf-sync/models_mflux.json "$dest/data-hf-sync/"
    cp orchestrator-local/justfile orchestrator-local/README.md orchestrator-local/pyproject.toml "$dest/"
    cp orchestrator-local/.env.sample "$dest/.env.sample"
    echo "Synced code+config+deployment files to $dest (data/reports.sqlite, data/models_hf.json, and .env there were left untouched)"
