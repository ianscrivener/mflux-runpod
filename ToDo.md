# Status (unattended session, 2026-08-17)

# Tasks

0.  ✅ Core structure and SQLite database
1.  ✅ use `data/models_mflux.json` for now (/models_supported)
2.  ✅ /models_hf  & /models_hf/update
3.  ✅ /models_missing
4.  ✅ configs/overrides.yaml (manual force-include/exclude) folded into /models_missing
5.  ✅ Orchestrator: create/reuse per-model-series ephemeral RunPod volume (live-tested against
    real RunPod API: create, reuse-on-duplicate, get, delete). HF source download into the
    volume: `app/series_lifecycle.py::download_source_weights()` written (mirrors old
    cpu-admin-hf-cache.py), untested live — needs an actual mounted-volume/pod context to
    verify, which is GPU/live-billing territory (see status note above).
6.  ✅ Runner (GPU): `app/runner.py` — ports create-mflux-models.py's dynamic model-class
    lookup, per-quant build+upload loop, HF Collection grouping. 11 tests, all mocked
    (fake mflux model class, fake HfApi) — mflux itself is never imported in tests. No
    @Endpoint decorator; not deployed.
7.  ⚠️ partial — Orchestrator -> Runner trigger exists as an injectable `trigger_fn` on
    generate_one/generate_all (app/generate.py), defaulting to a no-op dry run. Runner ->
    Orchestrator callback exists: `POST /report/run/{run_id}` (app/main.py), tested via
    FastAPI TestClient. What's NOT done: an actual live trigger_fn that calls a real deployed
    Runner endpoint — there is no deployed Runner to call yet (needs 14/15 first).
8.  ✅ /generate  (single/force/branch override, per PRD) — see decision #1 above re:
    config_stem vs hf_model_name.
9.  ✅ /generate_all
10. ✅ /report  (reads/writes reports.sqlite: runs + quant_builds tables) — `app/report.py`,
    plus GET /report (recent/by-series/by-run-id) and the POST /report/run/{run_id} callback.
11. ✅ /health & /ping
12. ✅ Ephemeral volume cleanup — `app/series_lifecycle.py`: delete_source_weights() (frees
    the source/ subdir once a series' quants are built), teardown_if_complete() (deletes the
    whole volume only once every quant in the config is live on HF). 10 tests, all local
    tmp_path + monkeypatched RunPod calls — no live deletes performed here.
13. ✅ Crash-resume (sha256 manifest.json check) — `app/runner.py::is_locally_valid()` /
    `hash_dir()`, ported from create-mflux-models.py. Covered by test_runner.py.
14. ⏸ BLOCKED ON YOU — runpod.yaml wiring: confirm real GPU/CPU machine types & timeouts
    against RunPod account limits. Needs a decision from you, not just code.
15. ⏸ BLOCKED ON YOU — End-to-end dry run on one small model series (e.g. Flux.1-Schnell)
    before running full backlog. This means real GPU billing + a real HF upload. Needs your
    explicit go-ahead on which model and when, not something to run unattended.
16. ✅ openapi / swagger interface & swagger.json, openapi.json etc
17. ✅ GitHub Actions to deploy & tear down — .github/workflows/deploy.yml
    (workflow_dispatch, deploy/undeploy choice, `uv run flash deploy` /
    `flash undeploy --all --force`, RUNPOD_API_KEY from repo secrets — you've added the
    secret). Live-tested `flash deploy`/`flash app delete` against the real account and
    cleaned up (twice — see below). Still needed: wrap the Orchestrator as a real Flash
    @Endpoint (currently app/main.py is a plain FastAPI app with no @Endpoint, so
    `flash deploy` today only creates an empty app record, not a working serverless
    endpoint) — part of 14/15.

**Housekeeping done this session:** fixed a late-binding default-argument bug in
`app/db.py` (`init_db`/`get_connection` were capturing `DB_PATH` at import time,
so tests that monkeypatched it were silently sharing one real database across
runs — now re-read at call time). Added `data/reports.sqlite` and
`data/models_hf.json` to `.gitignore` (generated, not source); removed the
stale `z_ToDo.txt` ignore entry (file was renamed to `ToDo.md`). Found and
deleted a leftover `mflux-runpod` Flash app record on the live RunPod account
from an earlier deploy test that I'd initially thought was already cleaned up
— confirmed clean now via `flash app list` / `runpodctl network-volume list`.
