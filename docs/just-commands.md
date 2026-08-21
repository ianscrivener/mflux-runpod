# Just Commands

Reference for this project's `justfile` (repo root). Run `just` with no
arguments to list all recipes; run `just <recipe>` to execute one.

| Command | Usage | Description |
|---|---|---|
| `test` | `just test` | Runs the fast unit test suite (`pytest tests/ -q`) — mocked, no live network calls. |
| `json` | `just json` | Validates every JSON/JSONL file under `data/` and `data-hf-sync/` (`scripts/validate_json.py`) — catches a syntax mistake in a hand-edited file (e.g. `models_skipped.json`) before it 500s the app. |
| `serve` | `just serve` | Runs the Orchestrator locally in the foreground with `--reload`, at `http://127.0.0.1:8000/`. Also serves the built admin web app if `app/static/` exists (see `webapp-build`). |
| `webapp-build` | `just webapp-build` | Builds the admin web app (`webapp/`, Svelte + Vite) into `app/static/`, so `serve`/the launchd service can serve it. Re-run after any `webapp/src` change. |
| `webapp-dev` | `just webapp-dev` | Runs the web app's own Vite dev server with hot reload at `http://127.0.0.1:5173`, proxying API calls to `:8000`. Run `just serve` in another terminal first. |
| `svc-add` | `just svc-add` | Installs and starts the Orchestrator as a macOS launchd service (`com.ianscrivener.mflux-orchestrator`) — runs at login, restarts on crash, binds `127.0.0.1:8000` only. Logs to `logs/orchestrator.{out,err}.log`. Safe to re-run to pick up a plist change. |
| `svc-del` | `just svc-del` | Stops and uninstalls the launchd service installed by `svc-add`. |
| `test-api` | `just test-api` | Hits the local Orchestrator's endpoints and prints a one-line summary each (`scripts/test_api.py`). Always targets port 8000 — starts it in the background first if nothing's listening there yet, and only stops it again afterward if this recipe was the one that started it. |
| `health` | `just health` | `GET /health` against the local dev server, pretty-printed JSON. |
| `models_mflux` | `just models_mflux` | `GET /models_mflux`, pretty-printed JSON. |
| `models_hf` | `just models_hf` | `GET /models_hf`, pretty-printed JSON. |
| `models_missing` | `just models_missing` | `GET /models_missing`, pretty-printed JSON. |
| `report` | `just report` | `GET /report`, pretty-printed JSON. |
| `open` | `just open` | Opens the local Orchestrator's `/docs` (FastAPI Swagger UI) in your browser. |
| `update-docker-runner` | `just update-docker-runner` | Syncs the GPU worker's shared code (`app/__init__.py`, `app/runner.py`, `app/models_missing.py`, `app/outbox.py`) into `docker-runner-hf/app/` — the standalone git checkout HF Spaces builds from. Doesn't touch `docker-runner-hf/.git`, `Dockerfile`, `worker.py`, or `README.md` (those are authored directly there). Doesn't commit or push. |
| `hf-push` | `just hf-push "commit message"` | Runs `update-docker-runner`, then commits and pushes `docker-runner-hf/` to the HF Space, **triggering a real rebuild there**. The commit message is a **positional** argument — `just hf-push "fixed the mlx pin"`, not `just hf-push msg="..."` (that syntax silently bakes a literal `msg=` prefix into the commit message). Defaults to `"Update GPU worker"` if omitted. Refuses to push if an unexpected file shows up in `docker-runner-hf/` (only `Dockerfile`, `worker.py`, `README.md`, `.gitignore`, `app/` are expected). No-ops cleanly if there's nothing to commit after the sync. |

## Notes

- Recipes prefixed with `_` (e.g. `_fetch`) are private helpers used by other
  recipes, not meant to be called directly.
- `hf-push` is the one recipe here with real, external, billed
  consequences — it pushes to the live HF Space, which starts a rebuild.
  Everything else is local-only.
