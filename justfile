# List available recipes
default:
    @just --list

# Run the fast unit test suite (mocked, no live network calls)
test:
    .venv/bin/pytest tests/ -q

# Hit the live Orchestrator's endpoints and print a one-line summary each
test-api:
    .venv/bin/python3 scripts/test_api.py

# GET /health, raw JSON
health:
    .venv/bin/python3 scripts/test_api.py /health

# GET /models_supported, raw JSON
models_supported:
    .venv/bin/python3 scripts/test_api.py /models_supported

# GET /models_hf, raw JSON
models_hf:
    .venv/bin/python3 scripts/test_api.py /models_hf

# GET /models_missing, raw JSON
models_missing:
    .venv/bin/python3 scripts/test_api.py /models_missing

# GET /model_store, raw JSON
model_store:
    .venv/bin/python3 scripts/test_api.py /model_store

# GET /report, raw JSON
report:
    .venv/bin/python3 scripts/test_api.py /report
