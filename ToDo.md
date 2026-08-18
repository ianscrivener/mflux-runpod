# Status (2026-08-18)

Flash path works end-to-end (real Fibo bf16 built + uploaded to HF).
Orchestrator deployed; SQLite persistence across worker scale-to-zero is
PROVEN (a run row survived worker death + a full redeploy). Swagger is live
at `https://<endpoint-id>.api.runpod.ai/docs` — needs an
`Authorization: Bearer <RUNPOD_API_KEY>` header (RunPod authenticates LB
endpoints at the edge; every path 401s without it, including `/ping`).

Docker runner path: PARTIALLY proven. Image builds+pushes to GHCR, container
starts on CUDA 13.0.1, GPU healthy, handler registers, container-start
`uv pip install` of mlx/mflux succeeds, HF download begins. NOT proven: a
completed quantize+upload — two attempts ended for environmental reasons (an
HF Xet transfer error, then the endpoint being deleted mid-job).

# Tasks

0.  ✅ Core structure and SQLite database
1.  ✅ use `data/models_mflux.json` for now (/models_supported)
2.  ✅ /models_hf  & /models_hf/update
3.  ✅ /models_missing
4.  ✅ configs/overrides.yaml (manual force-include/exclude) folded into /models_missing
5.  ✅ Orchestrator: create/reuse per-model-series ephemeral RunPod volume (live-tested against
    real RunPod API: create, reuse-on-duplicate, get, delete).
6.  ✅ Runner (GPU): `app/runner.py` — dynamic model-class lookup, per-quant build+upload,
    HF Collection grouping. Restructured to one-quant-per-job:
    `build_and_upload_one_quant()`. Tests mocked; mflux itself never imported in tests.
7.  ⚠️ partial — `trigger_fn` on generate_one/generate_all (app/generate.py) still defaults
    to a no-op dry run. The Runner IS now live and reachable, so a real trigger_fn could be
    wired today — just not done yet. Runner → Orchestrator callback exists and is tested
    (`POST /report/run/{run_id}`), and status is derived from all reported quant_builds
    (update_run_status_from_children) rather than trusted from any single job, since N
    quant jobs report against the same run_id.
8.  ✅ /generate
9.  ✅ /generate_all
10. ✅ /report (reports.sqlite: runs + quant_builds) — GET /report, POST /report/run/{run_id}
11. ✅ /health (Orchestrator's /ping route was dropped — Flash reserves that path for its
    own framework health check; /health covers the same purpose)
12. ✅ Ephemeral volume cleanup — `app/series_lifecycle.py`
13. ✅ Crash-resume (sha256 manifest.json check) — `app/runner.py`
14. ✅ Runner deployed as a real Flash `@Endpoint` — `app/runner_endpoint.py`, `mflux-runner`.
    GPU: ADA_24 (RTX 4090 tier), min_cuda_version=13.0, workers=(0,1).
15. ✅ End-to-end live test done: Fibo bf16 built + uploaded to
    `mflux-community/fibo-mflux-bf16` via a real dispatched job (2026-08-17,
    ~150s execution). Confirmed via HF API lastModified matching the run time.
16. ✅ openapi / swagger interface & swagger.json, openapi.json etc
17. ✅ GitHub Actions deploy/undeploy — `.github/workflows/deploy.yml`. Auto-deploys on every
    push to `main` (deploy job); `undeploy` stays manual-only (`workflow_dispatch`,
    `action: undeploy`) — fixed a race where a manual undeploy dispatch could also trigger
    deploy. Clean-slate step runs `flash undeploy <name> --force` for all three endpoints
    before `flash deploy`, since `flash deploy` always creates new endpoints rather than
    updating existing ones by name. A pre-flight "Verify RunPod credentials" step hits the
    REST API directly and fails fast on 401/403, because `flash undeploy`'s exit code can't
    distinguish "endpoint not found" from "bad API key" (both print the same message/exit 0).
18. ✅ Orchestrator deployed as a real Flash `@Endpoint` — `app/orchestrator_endpoint.py`,
    `mflux-orchestrator` (load-balanced/Mode 2 routes wrapping app/main.py's existing
    app/*.py logic). Live-verified: mount path for `volume=NetworkVolume(...)` is
    `/runpod-volume` (confirmed via a throwaway probe, since removed); `/health` route
    returns 200. NOT yet verified: whether reports.sqlite data on that volume actually
    survives a worker scaling to zero (idle_timeout=60s) — this is the real test of
    whether the persistence design works, and hasn't been run yet.
19. GPU CUDA-13 runtime fixes (mflux/mlx on RunPod):
    - `mlx-cuda-13`/`mflux` are deliberately NOT in the Runner's `dependencies=[]` — Flash's
      `flash deploy` bundler cross-compiles with `--only-binary=:all:` against a manylinux
      platform list that doesn't include mlx-cuda-13's actual wheel tag
      (manylinux_2_35_x86_64), and separately can't install mflux's git-source dependency
      under `--only-binary=:all:` at all. Fixed by installing both at worker runtime instead
      (native linux_x86_64, no cross-platform constraints), guarded by a marker file so a
      warm worker only pays the cost once.
    - `mlx[cuda13]`'s pip-installed NVIDIA libs (cublas/cudnn/nccl/cufft/nvrtc) aren't on the
      dynamic linker's search path by default (`libcublasLt.so.13: cannot open shared object
      file`). Setting `LD_LIBRARY_PATH` at runtime does NOT fix this (glibc's loader reads it
      once at process startup, before our code runs). Fixed by preloading each `.so` via
      `ctypes.CDLL(..., mode=ctypes.RTLD_GLOBAL)` before mlx is imported.
    - `HF_TOKEN` now comes from a RunPod Secret (`{{ RUNPOD_SECRET_HF_TOKEN }}`) rather than
      being unset — confirmed working via the health-check job's `hf_token_configured: true`.
20. ✅ `mflux-runner-health` — a second, separate Flash `@Endpoint` (own worker pool) that
    imports mlx/mflux and reports the CUDA device without doing a full model build. Exists
    because Flash's deployed-handler generator only wires up `functions[0]` of a resource —
    two `@Endpoint`-decorated functions on ONE Endpoint silently drops the second one
    entirely (confirmed by reading runpod_flash's handler_generator.py + a real build
    manifest). `scripts/check_runner_health.py` dispatches to it and is wired into CI as a
    post-deploy gate (fails the job if status != "ok" or HF_TOKEN didn't inject correctly).
21. Legacy cleanup: `mflux-save.py` (pre-restructure standalone script, superseded by
    app/runner.py) moved to `docs/legacy/` — it was breaking every `flash deploy` because
    Flash's project-wide file scan aborted on its unmet dependency (huggingface_hub not
    installed in the deploy environment). `docs/` is one of Flash's built-in ignored paths.

## Known issues / not yet done

- **Persistence not proven** (see 18): need to write a run via `/generate`, read it back via
  `/report`, wait >90s past idle_timeout, then read again to confirm the row survives a
  worker restart. This is the load-bearing claim behind using SQLite-on-NetworkVolume at all.
- **trigger_fn still dry-run only** (see 7): `/generate_all` plans and records runs but
  doesn't dispatch real GPU work. Wiring a real trigger_fn is the next functional gap —
  the Runner is live and provably works, it's just not called automatically yet.
- **Runner ↔ Orchestrator callback loop untested live**: `ORCHESTRATOR_BASE_URL` has never
  been set on the deployed Runner (it only exists now that the Orchestrator has a stable
  URL) — the Fibo bf16 test ran with no run_id/callback, so `update_run_status_from_children`
  has only been tested via FastAPI TestClient, never against a real callback POST.
- **`/report/dump` route not wired**: `app/report.py::dump_all()` is written and tested
  (full raw JSON of runs + quant_builds + series_volumes + summary), but no route exposes
  it yet in `app/orchestrator_endpoint.py` / `app/main.py`.
- **`flash deploy` fails with "endpoint template names must be unique"** on repeat deploys.
  Cause: `flash undeploy <name> --force` deletes the endpoint but orphans its *template*,
  so the next deploy collides. This is a consequence of the clean-slate undeploy step in
  deploy.yml. Likely fix: remove that step — evidence suggests `flash deploy` DOES update
  in place (it logged "LiveServerless:... is no longer valid, redeploying"), and the
  duplicates that motivated the step came from `.flash/resources.pkl` divergence plus
  out-of-band MCP deletions, not from deploy itself.
- `configs/Qwen-Image-Layered.yaml` still has `hf_model_name: null` — needs manual research.
- Runner `workers=(0, 1)` still capped for safe one-at-a-time testing — raise once you're
  comfortable with concurrent GPU jobs.
- `RUNPOD_API_KEY` is visible in plaintext via `list-endpoints`' `env` field for every
  deployed endpoint (confirmed via the RunPod MCP tool, both CI's key and, briefly, a local
  `flash login` key that leaked into a manually-triggered deploy) — worth rotating once
  testing settles down, and worth checking whether Flash has a way to suppress this.
- Repeated CI runs during this session created several rounds of duplicate endpoints
  (`flash deploy` doesn't update-in-place; the clean-slate undeploy step sometimes lagged
  behind new resources like `mflux-orchestrator` being added). Manually cleaned up multiple
  times via the RunPod MCP tools. Current account state (as of this writing) is the expected
  3 endpoints: `mflux-runner`, `mflux-runner-health`, `mflux-orchestrator`.
