# MFlux Orchestrator (local runtime)

Standalone local copy of the Orchestrator half of
[mflux-runpod](https://github.com/ianscrivener/mflux-runpod). Runs the CPU-only
FastAPI app that scans the MFlux-supported models list against the
`mflux-community` Hugging Face org, diffs them into a missing-models list, and
(optionally) dispatches GPU build jobs to the Runner. See that repo's
`ARCHITECTURE.md` for the full picture.

**This folder is a deployment, not the source repo.** `app/*.py` and
`configs/models/*.yaml` here are copies, kept in sync by running `just
update-orchestrator` from the `mflux-runpod` checkout -- don't hand-edit them
here, they'll be overwritten on the next sync. `data/reports.sqlite` and
`data-hf-sync/models_hf.json` are this deployment's own live runtime state and are
never touched by that sync.

## Layout

```
app/                  Orchestrator-only routes/logic (no GPU/mflux/Flash code)
  main.py             FastAPI app + all routes -- run this
  db.py, generate.py, models_hf.py, models_missing.py, models_supported.py,
  outbox.py, report.py, runpod_volumes.py, series_lifecycle.py

configs/models/*.yaml   Per-model build config -- source of truth for what's buildable
configs/overrides.yaml, configs/hf_datasets.yaml   Singleton configs, siblings of models/
data-hf-sync/models_mflux.json Full MFlux-supported model catalog
data-hf-sync/models_hf.json    Cache of what's published on the HF org (runtime state)
data/reports.sqlite    Run/quant-build history (runtime state)

pyproject.toml         Trimmed deps -- fastapi/uvicorn/pyyaml/huggingface-hub/httpx/boto3.
                        No mflux, no runpod-flash: this process never touches a GPU
                        or deploys anything, it only plans and dispatches.
justfile                svc-add/svc-del (launchd service) -- see below. Local to this
                        folder, separate from mflux-runpod's own justfile.
```

## Setup

```bash
uv sync
```

Then fill in `.env`:

| Var | Required for | Notes |
|---|---|---|
| `HF_TOKEN` | everything that touches HF | write-scoped token for the `mflux-community` org |
| `HF_ORG` | optional | defaults to `mflux-community` |
| `RUNPOD_API_KEY` | `POST /generate` with `dispatch: true` | |
| `RUNNER_ENDPOINT_ID` | `POST /generate` with `dispatch: true` | the GPU Runner's RunPod endpoint id -- currently `jx45e9ewmop06z` (`mflux-runner-docker-test`), already set |
| `DO_SPACES_KEY` / `DO_SPACES_SECRET` / `DO_SPACES_REGION` / `DO_SPACES_ENDPOINT` / `DO_SPACES_BUCKET` | `POST /outbox/poll` | durable Runner→Orchestrator result delivery (see `app/outbox.py`) |

## Run (foreground, for dev)

```bash
uv run uvicorn app.main:app --reload
```

Then visit `http://127.0.0.1:8000/docs`, or curl directly:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/models_supported
curl http://127.0.0.1:8000/models_hf
curl -X POST http://127.0.0.1:8000/models_hf/update
curl http://127.0.0.1:8000/models_missing
curl http://127.0.0.1:8000/model_store
curl http://127.0.0.1:8000/report
curl -X POST http://127.0.0.1:8000/outbox/poll
```

`REPORT_DB_PATH`/`MODELS_HF_PATH` don't need setting -- they resolve relative
to this folder automatically (see `app/db.py` / `app/models_hf.py`), as long
as you run `uv run` from here.

## Run as a background service (launchd)

For a persistent local Orchestrator that survives terminal/reboot without a
foreground shell:

```bash
just svc-add    # installs + starts com.ianscrivener.mflux-orchestrator, binds 127.0.0.1:8000
just svc-del    # stops + uninstalls it
```

`svc-add` writes `~/Library/LaunchAgents/com.ianscrivener.mflux-orchestrator.plist`
(`RunAtLoad` + `KeepAlive`, so it starts at login and restarts on crash) and logs
to `logs/orchestrator.{out,err}.log` in this folder. Check it's up with
`launchctl list | grep mflux-orchestrator` or `curl http://127.0.0.1:8000/health`.

## Syncing code from the source repo

From the `mflux-runpod` checkout:

```bash
just update-orchestrator
```

Copies the allowlisted `app/*.py` files, `configs/models/*.yaml` plus
`configs/overrides.yaml`/`configs/hf_datasets.yaml`, and
`data-hf-sync/models_mflux.json` here. Never touches `data/reports.sqlite` or
`data-hf-sync/models_hf.json`.
