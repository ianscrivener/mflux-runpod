# mflux-runpod

Services that convert and quantize AI models for [MFlux](https://pypi.org/project/mflux/)
(its GitHub repo, mflux-community/mflux, was suspended by GitHub 2026-08-20 -- PyPI is
now the only supported install source),
and keep the [mflux-community](https://huggingface.co/mflux-community) Hugging Face organization in sync.
Replaces an earlier Modal-based implementation that became too expensive to run.

**`hf-gpu-worker` branch**: migrated the GPU worker off RunPod (removed) to a
Hugging Face Spaces Docker worker (`docker-runner-hf/`, a separate
repo/deployment) -- see [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full
picture, including what's still not implemented as a result (`/generate/{run_id}/cancel`,
mainly).

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
  models_mflux.py     /models_mflux — reads data-hf-sync/models_mflux.json
  models_hf.py         /models_hf, /models_hf/update — scans the mflux-community HF org
  models_missing.py    /models_missing — diffs configs/models/*.yaml against models_hf.json

configs/models/*.yaml  Per-model build config (model_object, model_config, quants, collection).
                        Only models with a config here are eligible for /models_missing and
                        generation — configs/models/, not data-hf-sync/models_mflux.json, is the
                        source of truth for what's buildable.
configs/overrides.yaml  Manual force_include/force_exclude overrides for /models_missing.
configs/hf_datasets.yaml  HF-bucket sync locations for the seven datasets (app/hf_datasets.py).

data-hf-sync/models_mflux.json Snapshot of models supported by MFlux (full catalog, task 1 placeholder
                        for a live GitHub scan).
data-hf-sync/models_hf.json    Generated manifest of what's currently published on the HF org.

tests/                 pytest suite

PRD.md                 Full product/design spec
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
curl http://127.0.0.1:8000/models_mflux
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

Core structure, SQLite reporting schema, and the read-only `/models_mflux`,
`/models_hf(/update)`, and `/models_missing` endpoints are implemented and tested. The GPU
Runner, ephemeral per-model-series volumes, `/generate`, `/generate_all`, and `/report` are not
yet built — see [z_ToDo.txt](z_ToDo.txt) for the remaining steps.
