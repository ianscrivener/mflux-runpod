# Tasks

0.  ✅ Core structure and SQLite database
1.  ✅ use `data/models_mflux.json` for now (/models_supported)
2.  ✅ /models_hf  & /models_hf/update
3.  ✅ /models_missing
4.  ✅ configs/overrides.yaml (manual force-include/exclude) folded into /models_missing
5.  ✅ Orchestrator: create/reuse per-model-series ephemeral RunPod volume (live-tested against
    real RunPod API: create, reuse-on-duplicate, get, delete). HF source download into the
    volume is still TBD — needs a Runner/pod context to write to the mounted volume.
6.  Runner (GPU): read configs/{model}.yaml, build+upload quants, HF Collection grouping
7.  Async trigger: Orchestrator -> Runner, and Runner -> Orchestrator status callback
8.  /generate  (single/force/branch override, per PRD)
9.  /generate_all
10. /report  (reads/writes reports.sqlite: runs + quant_builds tables)
11. ✅ /health & /ping
12. Ephemeral volume cleanup (delete source weights post-build; delete volume post-verify)
13. Crash-resume (sha256 manifest.json check on cached builds, matches old create-mflux-models.py)
14. runpod.yaml wiring: confirm real GPU/CPU machine types & timeouts against RunPod account limits
15. End-to-end dry run on one small model series (e.g. Flux.1-Schnell) before running full backlog
16. ✅ openapi / swagger interface & swagger.json, openapi.json etc
17. GitHub Actions to deploy & tear down — .github/workflows/deploy.yml added
    (workflow_dispatch, deploy/undeploy choice, `uv run flash deploy` /
    `flash undeploy --all --force`, RUNPOD_API_KEY from repo secrets). Live-tested
    `flash deploy`/`flash app delete` against the real account and cleaned up.
    Still needed: add RUNPOD_API_KEY as a GitHub repo secret (Settings > Secrets
    and variables > Actions), and wrap the Orchestrator as a real Flash
    @Endpoint (currently app/main.py is a plain FastAPI app with no @Endpoint,
    so `flash deploy` today only creates an empty app record, not a working
    serverless endpoint) — that lands with task 6+.