# mflux-runpod

RunPod Flash services that convert and quantize AI models for [MFlux](https://github.com/mflux-community/mflux),
and keep the [mflux-community](https://huggingface.co/mflux-community) Hugging Face organization in sync.
Replaces an earlier Modal-based implementation that became too expensive to run.

See [PRD.md](PRD.md) for the full design (endpoints, storage lifecycle, reporting schema) and
[z_ToDo.txt](z_ToDo.txt) for build progress.

## Architecture

Two services:

- **Orchestrator (CPU)** — scans the MFlux-supported models list and the `mflux-community` HF
  org, diffs them into a missing-models list, stages source weights into per-model ephemeral
  volumes, and triggers the GPU Runner.
- **Runner (GPU)** — builds each missing quant with mflux, uploads it to its own HF repo, and
  groups quants into an HF Collection.

## Project layout

```
app/                  Orchestrator FastAPI app
  main.py             API routes
  db.py               SQLite schema (runs, quant_builds) for /report
  models_supported.py /models_supported — reads data/models_mflux.json
  models_hf.py         /models_hf, /models_hf/update — scans the mflux-community HF org
  models_missing.py    /models_missing — diffs configs/*.yaml against models_hf.json

configs/*.yaml         Per-model build config (model_object, model_config, quants, collection).
                        Only models with a config here are eligible for /models_missing and
                        generation — configs/, not data/models_mflux.json, is the source of
                        truth for what's buildable.

data/models_mflux.json Snapshot of models supported by MFlux (full catalog, task 1 placeholder
                        for a live GitHub scan).
data/models_hf.json    Generated manifest of what's currently published on the HF org.

tests/                 pytest suite

PRD.md                 Full product/design spec
runpod.yaml             Sample RunPod Flash machine config (CPU orchestrator, GPU runner)
z_ToDo.txt              Task list / build order
```

## Setup

```bash
uv sync
```

Requires `HF_TOKEN` (write-scoped, for the `mflux-community` org) and optionally `HF_ORG`
(defaults to `mflux-community`) in the environment.

## Running the Orchestrator locally

```bash
uv run uvicorn app.main:app --reload
```

Then visit `http://127.0.0.1:8000/docs` for the interactive Swagger UI, or:

```bash
curl http://127.0.0.1:8000/models_supported
curl http://127.0.0.1:8000/models_hf
curl -X POST http://127.0.0.1:8000/models_hf/update
curl http://127.0.0.1:8000/models_missing
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ping
```

## Tests

```bash
uv run pytest -v
```

## Status

Core structure, SQLite reporting schema, and the read-only `/models_supported`,
`/models_hf(/update)`, and `/models_missing` endpoints are implemented and tested. The GPU
Runner, ephemeral per-model-series volumes, `/generate`, `/generate_all`, and `/report` are not
yet built — see [z_ToDo.txt](z_ToDo.txt) for the remaining steps.
