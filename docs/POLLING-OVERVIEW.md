# Polling overview

Every place this project polls something on a timer, backend and frontend.

## Backend (Orchestrator background loops, `app/main.py`)

Started in `lifespan()` as `asyncio` tasks; intervals come from
`configs/config.yaml`'s `polling:` block (`load_polling_config()`), not
hardcoded, so they're tunable without a code change.

| Loop | File | Interval | Skips scan if fresh? | What it does |
|---|---|---|---|---|
| `_outbox_poll_loop` | `app/main.py` | 30s (`outbox_poll_interval_s`) | no | Drains the HF-bucket outbox (`app/outbox.py::process_pending`) -- applies any GPU-worker job results dropped there since the last poll. Disables itself (returns) if `HF_TOKEN` isn't set. |
| `_models_hf_refresh_loop` | `app/main.py` | 30s (`models_hf_refresh_interval_s`) | yes -- skips the HF org scan if `data-hf-sync/models_hf.json` was written within the last interval | Rescans the `mflux-community` HF org (`app.models_hf.update_models_hf`) and rebuilds the catalog mirror. Disables itself if `HF_TOKEN` isn't set. |
| `_models_src_details_refresh_loop` | `app/main.py` | 21600s / 6h (`models_src_details_refresh_interval_s`) | yes -- same staleness check against `data-hf-sync/models_src_details.json` | Rescans every model's *upstream* source repo (size, commit hash, last-modified, text encoder) via `app.models_src_details.refresh_models_src_details`. Long interval: one HF API call per distinct source repo, and those change on the order of days/weeks. Disables itself if `HF_TOKEN` isn't set. |
| `_hf_sync_loop` | `app/main.py` | 300s / 5m (`hf_sync_interval_s`) | yes, implicitly -- `pull()` is metadata-only when nothing changed | Pulls every dataset in `configs/hf_datasets.yaml` (`models_mflux`, `models_missing`, `models_queue`, etc.) from the HF bucket, so this process's `data-hf-sync/` mirror stays current with any other writer (or the upstream mflux CI pipeline, for `models_mflux`). Exits at startup, once, if `HF_TOKEN` isn't set (checked before the loop starts, not inside it). |

All four loops rebuild the SQLite catalog mirror (`app.models_catalog.rebuild_if_needed`) after their own work, and none of them crash the process on a single bad iteration -- each catches its own exceptions and logs, then sleeps and retries next cycle.

## Frontend (Svelte views, `webapp/src/`)

Plain `setInterval`, hardcoded per-view (not centrally configured); each view's own `load()` re-fetches, and every one cleans up its interval in `$effect(() => () => clearInterval(poll))` on unmount.

| View | File | Interval | Endpoint(s) polled |
|---|---|---|---|
| `App` (topbar online/offline dot) | `webapp/src/App.svelte` | 10000ms | `GET /health` |
| `ModelsView` | `webapp/src/lib/views/ModelsView.svelte` | 8000ms | `GET /models_available`, `GET /models_src_details`, `GET /text_encoder_aliases` |
| `DatasetsView` (admin) | `webapp/src/lib/views/DatasetsView.svelte` | 10000ms | `GET /datasets` |
| `QueueView` | `webapp/src/lib/views/QueueView.svelte` | 6000ms | `GET /models_queue` |
| `RunsView` (Generate) | `webapp/src/lib/views/RunsView.svelte` | 6000ms | `GET /report`, `GET /models_missing`, `GET /report/dump` |
| `GpuView` | `webapp/src/lib/views/GpuView.svelte` | 5000ms | `GET /gpu/status` |

Not polled: `GpuView`'s hardware-pricing table and worker logs (`GET /gpu/hardware`, `GET /gpu/logs/build`, `GET /gpu/logs/container`) load once on mount / on manual Refresh click only -- not worth hitting on a timer since the pricing data is static and the logs are pulled fresh from the HF Space's own buffer on every request anyway.
