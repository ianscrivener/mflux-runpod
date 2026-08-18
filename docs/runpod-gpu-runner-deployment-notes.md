# RunPod GPU Runner deployment — lessons learned (2026-08-18)

Notes from standing up the Docker-image GPU Runner (`dockerFiles/runner.dockerfile`
+ `dockerFiles/runner_handler.py`) as a live RunPod Serverless endpoint and
diagnosing why the first dispatches didn't work. Written for whoever next
touches this deployment path.

## Current known-good endpoint config

Endpoint `mflux-runner-docker-test` (manually created for testing — **not**
wired to the Orchestrator's `trigger_fn`, which is still a dry-run no-op):

```json
{
  "image": "ghcr.io/ianscrivener/mflux-runpod/mflux-runner:<git-sha>",
  "registry": "<ghcr-mflux-runpod container registry auth id>",
  "gpu": { "pools": ["ADA_24"] },
  "allowedCudaVersions": ["13.0", "13.2"],
  "dataCenterIds": ["US-IL-1"],
  "disk": 100,
  "env": { "HF_TOKEN": "{{ RUNPOD_SECRET_HF_TOKEN }}" },
  "workers": { "min": 0, "max": 1, "idleTimeout": 60 }
}
```

Three settings above exist specifically because of failures diagnosed
below: `allowedCudaVersions`, `dataCenterIds`, and using a SHA tag instead
of `:latest`.

## 1. `mlx-cuda-13==0.32.0` breaks quantized matmul on CUDA/Linux

`mflux` runs on Apple's MLX framework, which added an NVIDIA CUDA backend
(the `mlx-cuda-13` PyPI package). `mlx>=0.32.0` has a **regression in
`quantized_matmul` on the CUDA/Linux backend** — the kernel used for
quantized (q4/q6/q8) builds. bf16 builds never call that kernel, so they're
unaffected. `mflux`'s own `pyproject.toml` normally pins `mlx<0.32.0` to
work around this.

This repo's Docker runner image **intentionally bakes in `mlx==0.32.0`
anyway**, per explicit request, despite the known bug. `dockerFiles/runner_handler.py`
exposes a per-job override (`force_mlx_ver`, e.g. `"0.31.1"`) to pin below
0.32.0 for a specific quantized job without rebuilding the image. If a q4/q6/q8
build on this runner fails or produces bad output, **suspect this first** —
check the error for matmul/quantize/NaN symptoms before looking elsewhere.
The only proven-good live test so far (Fibo bf16, an earlier session) never
exercised this path.

## 2. The `mcp__runpod__create-endpoint` / `update-endpoint` MCP tools are a curated projection, not the full API

Two fields the real RunPod v2 REST API supports are **not exposed** by
either MCP tool: `allowedCudaVersions` and `dataCenterIds`. Missing either
one caused real failures (see below). When a RunPod MCP tool's result looks
like it's missing a field you'd expect, check
`https://api.runpod.io/v2/openapi.json` before assuming it isn't possible —
the tool's schema is not the ceiling of what the API supports.

**Workaround:** call the REST API directly for the missing fields.

```bash
curl -s -X PATCH "https://api.runpod.io/v2/serverless/<endpointId>" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"dataCenterIds": ["US-IL-1"], "allowedCudaVersions": ["13.0", "13.2"]}'
```

Two traps here:

- **Wrong domain.** `api.runpod.ai` is for job dispatch/status against a
  *specific endpoint ID* (`run`, `runsync`, `status`, `stream`, `cancel`).
  `api.runpod.io` is account-level management (create/list/update/delete
  endpoints, templates, volumes — `/v2/serverless`, `/v2/networkvolumes`,
  etc). Hitting the wrong one returns a **plain-text** `404 page not found`,
  easy to misread as "wrong path" rather than "wrong host."
- A raw call needs `RUNPOD_API_KEY` on the command line, which Claude Code's
  auto-mode permission classifier blocks by default. Needs an explicit
  scoped Bash permission rule, e.g.
  `Bash(curl -s -X PATCH https://api.runpod.io/v2/serverless/*:*)`.

## 3. `allowedCudaVersions` — without it, jobs can land on a host whose driver can't run the image

Symptom: job stuck `IN_QUEUE` forever, `list-endpoint-workers` shows
`unhealthy: 0` (misleading — the container never even started, so RunPod
doesn't count it as unhealthy). The real error only shows up in
`stream-worker-logs` (system source), repeating on every retry:

```
nvidia-container-cli: requirement error: unsatisfied condition: cuda>=13.0,
please update your driver to a newer version, or use an earlier cuda container
```

Our base image (`nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04`) requires a
host driver supporting CUDA 13.0+. The `ADA_24` (RTX 4090) pool spans hosts
with a mix of driver versions; with no constraint, RunPod's scheduler will
place a worker on *any* of them. Setting `allowedCudaVersions: ["13.0", "13.2"]`
(valid values come from `GET /v2/catalog/gpus?include=AVAILABILITY&product=SERVERLESS`,
i.e. `mcp__runpod__list-gpu-types`' `cudaVersions` field) restricts
placement to compatible hosts only.

## 4. GPU availability varies wildly by datacenter — pin to a high-availability DC

`RTX 4090` / `ADA_24` availability (serverless), captured 2026-08-18:

| DC | Availability |
|---|---|
| **US-IL-1** | **HIGH** |
| EU-RO-1, EUR-IS-2 | MEDIUM |
| CA-MTL-3, EU-CZ-1, EUR-IS-1, EUR-NO-1, US-CA-2, US-NC-1, US-TX-3 | LOW |

With `dataCenterIds` left empty (the default — "let the scheduler choose"),
a worker can land in a LOW-availability DC, where hosts are scarcer and more
contended. Observed symptom: worker status `THROTTLED`, and/or an image
pull that logs `image pull: <image>: pending` repeatedly **with zero byte
progress** for 10+ minutes — a dead pull, not a slow one. (Compare to a
healthy pull, which logs active `Extracting [===>...] X.XGB/Y.YGB` progress
within seconds.)

Fix: pin `dataCenterIds: ["US-IL-1"]` — also convenient because it's
already the `DEFAULT_DATA_CENTER_ID` in `app/runpod_volumes.py` for network
volumes (see §6), so GPU workers and any future series volume naturally
line up in the same DC.

**Not related: GHCR proximity.** It's tempting to think "pin near wherever
GHCR is hosted" — but GHCR (GitHub Packages, backed by Azure's global CDN)
has no single origin DC; pulls are served from whatever edge node is
closest to the pulling host, automatically, everywhere. The throttling
above is RunPod host-capacity contention, not registry network distance —
don't chase GHCR location as a fix.

## 5. No direct "kill worker" tool — force a scale-down instead

There's no MCP tool (or, per the openapi spec, REST endpoint) to directly
terminate a specific serverless worker. To force out a stuck/orphaned
worker with no live job attached:

1. `update-endpoint` with `workersMax: 0` — forces existing workers down
   since they now exceed the max.
2. Confirm via `list-endpoint-workers` that the worker list is empty.
3. `update-endpoint` with `workersMax: 1` (or whatever the real target is)
   to re-enable, ideally *after* applying any placement fixes
   (`allowedCudaVersions`, `dataCenterIds`) so the next worker doesn't
   repeat the same failure.

`purge-endpoint-queue` only drops *queued* jobs — it does nothing to a
worker that's already mid-pull/init with no job (which is exactly the
"orphaned worker" case above).

## 6. Storage model recap (confirmed against RunPod docs, not just this repo's code)

- **Network volumes are always datacenter-pinned.** No "global" volume
  option exists. Any compute mounting one must run in that same DC. To
  spread availability across DCs you'd need separate per-DC volume copies
  with manual sync (S3 API / `runpodctl`) — RunPod does not sync them for
  you.
- **Container/ephemeral disk** (what today's manual test dispatches used,
  `volume_root: "/tmp/mflux_build"`) is fast but wiped on scale-to-zero and
  never shared across workers. Fine for a one-off smoke test; **not** the
  intended production path.
- The intended production design already exists in this repo, just not
  wired up end-to-end yet:
  - `app/runpod_volumes.py::create_volume(model_series, data_center_id)` —
    per-series ephemeral network volume, DC-pinned (defaults to `US-IL-1`).
  - `app/series_lifecycle.py::download_source_weights()` — downloads a
    series' HF source weights into the mounted volume.
  - `app/runner.py::build_and_upload_one_quant()` — takes `volume_root` as
    the build workspace; this is the same function both the Flash and
    Docker Runner entrypoints call.
  - `app/series_lifecycle.py::teardown_if_complete()` — deletes the volume
    once every quant for the series is confirmed live on HF.
  - **Missing piece:** `app/generate.py`'s `trigger_fn` is still the
    no-op `dry_run_trigger` stub (ToDo task 7) — nothing currently calls
    `create_volume` → download → dispatch automatically. Today's manual
    dispatches bypassed this whole flow.

## 7. Orchestrator vs. GPU Runner run in different DCs, and that's fine

`mflux-orchestrator` (CPU, load-balancer endpoint) is pinned to `EU-RO-1` —
its own network volume (`reports.sqlite`, `models_hf.json`) lives there.
That's a **separate resource pool** (CPU) from the GPU Runner (`ADA_24`)
and has no bearing on GPU placement — don't read "orchestrator is in RO" as
a signal about where GPU capacity is. If/when a real per-series volume is
wired up for the GPU Runner (§6), it should be pinned to `US-IL-1` (GPU
availability), independent of where the Orchestrator's own CPU/volume
happens to sit.

## Quick diagnostic checklist for a stuck job

1. `get-job-status` → if `IN_QUEUE` with `workerHealth.unhealthy > 0`,
   that's a genuine crash-loop — go straight to `stream-worker-logs`.
2. `list-endpoint-workers` → check `status`. `THROTTLED` = host capacity
   contention (consider a DC pin). `INITIALIZING` for a long time with no
   job progress = check the pull.
3. `stream-worker-logs(source: "system", tail: 50+)` → look for
   `image pull: ...: pending` repeating with **no** `Extracting [...]` byte
   progress lines = dead pull, don't wait it out, kill and retry (§5).
   Look for `nvidia-container-cli: requirement error` = CUDA driver
   mismatch (§3).
4. If forcing a fresh worker: `workersMax:0` → confirm empty → fix
   placement constraints → `workersMax:1` (§5).
