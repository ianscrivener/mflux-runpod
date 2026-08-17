"""GPU Runner Flash @Endpoint (PRD: (2) Runner - GPU; task 14).

Wraps app/runner.py's build_and_upload_one_quant() as a Flash serverless
endpoint on CUDA 13, matching the mflux/mlx CUDA-13 preference.

Two SEPARATE Endpoints are defined here, each with exactly one function.
Flash's deployed-handler generator wires up only functions[0] of a resource
(confirmed by reading runpod_flash's handler_generator.py + a real
`flash build` manifest: two @runner-decorated functions on one Endpoint
silently produced a handler for only the first one, dropping the second
entirely) -- so run_generation and health_check cannot share one Endpoint
without one becoming unreachable:
  - runner / run_generation: the real build+upload job (see below).
  - runner_health / health_check: a cheap post-deploy smoke test -- imports
    mlx/mflux and reports the CUDA device without running a full model
    build. Same gpu/dependencies/env as `runner`, so its result is a
    faithful proxy for whether run_generation would get past its import
    step. Dispatch after `flash deploy` (e.g. scripts/check_runner_health.py
    in CI) to catch environment-level breakage (missing CUDA runtime libs, a
    bad HF_TOKEN secret reference) in seconds instead of discovering it
    after a multi-minute build fails.

One GPU job = one quant (decided with the user, 2026-08-17: better crash
isolation and retry granularity than batching a whole series into one job,
and mflux quant builds are minutes-long so per-job cold start is negligible
relative to build time). The Orchestrator's trigger_fn is responsible for
fanning a series' quants_to_build list out into one run_generation call per
quant — this module doesn't loop over quants itself.

Currently testing with workers=(0, 1) so failures surface one job at a time;
bump once the pipeline is proven (see runpod.yaml's runner.workers.max note).

NOT deployed by default — this module only defines the endpoint. Deploying it
(`flash deploy`) or running it against `flash dev` provisions/uses real, billed
RunPod GPU workers. Do that deliberately, not as a side effect of importing
this module (importing app.runner_endpoint does not create or call anything).

Per the Flash skill's Gotcha #1 (only the function body ships to `flash dev`
workers — module-level imports/constants are NOT included), every import and
constant each handler needs is defined *inside* that handler's own body
(run_generation and health_check each self-contained, deliberately
duplicating the small pip-install/CDLL-preload block rather than sharing a
module-level helper).

SECURITY: HF_TOKEN and the Orchestrator's callback base URL are both
deployment-scoped config (Endpoint env=), not request parameters:
  - HF_TOKEN as a request param would get written into worker-process env by
    a naive handler and then leak into whatever job that (reused, warm)
    worker handles next, regardless of whether that later request supplied
    its own token.
  - An attacker-suppliable callback URL turns this endpoint into an SSRF
    vector — it would let any caller make the worker POST arbitrary payloads
    to an arbitrary host. Only ORCHESTRATOR_BASE_URL, set once at deploy
    time, is used.
HF_TOKEN is set below via a RunPod Secret reference ({{ RUNPOD_SECRET_HF_TOKEN }}),
so its real value is injected at runtime and never appears in plaintext through
the RunPod API/console. ORCHESTRATOR_BASE_URL is set via `flash env` before
deploying — see runpod.yaml's runner.env block.
"""

from runpod_flash import CudaVersion, Endpoint, GpuGroup

# Shared by both Endpoints below so the health check's environment matches
# run_generation's exactly (same base image implied by gpu/min_cuda_version,
# same pip/apt deps, same secrets) -- otherwise a green health check
# wouldn't actually prove anything about the real job's environment.
_COMMON_KWARGS = dict(
    gpu=GpuGroup.ADA_24,  # RTX 4090 tier; matches runpod.yaml's gpu_type_std
    min_cuda_version=CudaVersion.V13_0,  # mflux/mlx has a hard CUDA 13 preference
    idle_timeout=60,
    dependencies=[
        "huggingface_hub",
        "pyyaml",
        "httpx",
        # mlx-cuda-13 and mflux are deliberately NOT listed here. Flash's
        # `flash deploy` bundler installs dependencies=[] with
        # `--platform manylinux_2_28/2_17/manylinux2014 --only-binary=:all:`
        # (cross-compiling for the worker from the CI/dev machine). Two things
        # break under that:
        #   1. mlx-cuda-13's only wheels are tagged manylinux_2_35_x86_64 --
        #      newer than every platform tag Flash requests, so pip reports
        #      "no matching distribution" even though the package is real and
        #      the pinned mflux range (>=0.30.3,<0.32.0 per mflux's own
        #      pyproject.toml) is satisfiable.
        #   2. `mflux @ git+...` is a source/VCS dependency, which
        #      --only-binary=:all: forbids building, independent of (1).
        # Neither is fixable by pinning a version here. Instead, install both
        # at worker runtime (native linux_x86_64, no cross-platform/binary-only
        # constraints) the first time this worker handles a request.
    ],
    system_dependencies=["libgl1", "libglib2.0-0"],
    env={
        "HF_XET_HIGH_PERFORMANCE": "1",
        # RunPod Secret (RunPod console/account) named HF_TOKEN, injected at
        # runtime -- never appears in plaintext via the RunPod API, unlike a
        # literal env value here would.
        "HF_TOKEN": "{{ RUNPOD_SECRET_HF_TOKEN }}",
        # Set at deploy time (flash env / RunPod console), not per-request:
        # "ORCHESTRATOR_BASE_URL": "https://<orchestrator-endpoint>",
    },
)

runner = Endpoint(
    name="mflux-runner",
    workers=(0, 1),  # one at a time while testing; raise once the pipeline is proven
    **_COMMON_KWARGS,
)

# Separate Endpoint (own worker pool) so it can't ever displace
# run_generation as functions[0] of a shared resource -- see module docstring.
runner_health = Endpoint(
    name="mflux-runner-health",
    workers=(0, 1),
    **_COMMON_KWARGS,
)


@runner
async def run_generation(
    config_stem: str,
    config: dict,
    quant: str,
    volume_root: str,
    force_hf_overwrite: bool = False,
    already_published: bool = False,
    run_id: int | None = None,
) -> dict:
    """Runs on a CUDA-13 GPU worker: builds+uploads exactly ONE quant, then
    (if run_id given and ORCHESTRATOR_BASE_URL is configured) POSTs a status
    report back to the Orchestrator's /report/run/{run_id} endpoint — that
    endpoint derives the run's aggregate status from every quant job's report
    rather than trusting a single job's opinion, since N of these run
    concurrently/sequentially against the same run_id.

    config is the resolved configs/{config_stem}.yaml dict (with any request
    overrides already applied by app.generate.resolve_generate_config) —
    passed as data, not re-read from disk, since the worker doesn't share the
    Orchestrator's filesystem. NOTE: mflux downloads source weights itself
    (via model_cls(...) pulling from the HF cache) — nothing here calls
    app.series_lifecycle.download_source_weights(); that function exists for
    a future pre-staging optimization but is not on this path yet.
    """
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    from huggingface_hub import HfApi

    # HF_TOKEN comes only from this endpoint's deployment env (see module
    # docstring) — never accepted as a request parameter, so a warm worker
    # can't leak one caller's credential into another caller's job.
    hf_api = HfApi(token=os.environ.get("HF_TOKEN"))

    started = time.monotonic()
    error = None
    result = None
    try:
        # mlx-cuda-13/mflux aren't in this Endpoint's dependencies=[] -- Flash's
        # deploy-time bundler cross-compiles with --only-binary=:all: against a
        # manylinux platform list that doesn't include mlx-cuda-13's actual
        # wheel tag (manylinux_2_35_x86_64), and separately can't install a
        # git-source package like mflux under --only-binary=:all: at all (see
        # the dependencies= comment above). Installing here runs natively on
        # the worker itself, where neither constraint applies. Guarded by a
        # marker file on the worker's local disk so a warm worker only pays
        # this once; a failed install is reported back like any other failure
        # instead of crashing the job unhandled.
        _mflux_marker = Path("/tmp/.mflux_runner_deps_installed")
        if not _mflux_marker.exists():
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "install", "--quiet",
                    "mlx[cuda13]>=0.30.3,<0.32.0",
                    "mflux @ git+https://github.com/mflux-community/mflux.git",
                ],
                check=True,
            )
            _mflux_marker.touch()

        # mlx[cuda13] pulls in nvidia-cublas/cudnn/nccl/cufft/cuda-nvrtc as pip
        # wheels (site-packages/nvidia/*/lib/*.so), but nothing adds that
        # directory to the dynamic linker's search path -- mlx's compiled
        # extension fails to dlopen them (e.g. "libcublasLt.so.13: cannot open
        # shared object file"). Setting LD_LIBRARY_PATH here does NOT help:
        # glibc's loader reads it once at process startup and caches the
        # result, so mutating os.environ mid-process is invisible to dlopen
        # calls in *this* process (it would only affect child processes).
        # Instead, preload each .so by absolute path with RTLD_GLOBAL before
        # mlx is imported -- that registers them by soname so mlx's own
        # dlopen (for e.g. libcublasLt.so.13) resolves against the
        # already-loaded object instead of searching the link path at all.
        import ctypes
        import glob
        import site

        site_dirs = [*site.getsitepackages(), site.getusersitepackages()]
        nvidia_libs = [
            so
            for site_dir in site_dirs
            for so in glob.glob(os.path.join(site_dir, "nvidia", "*", "lib", "*.so*"))
        ]
        # Load order matters for interdependent libs; retry failures once
        # after the first pass has resolved whatever they depended on.
        remaining = nvidia_libs
        for _ in range(2):
            still_failing = []
            for so in remaining:
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    still_failing.append(so)
            remaining = still_failing
            if not remaining:
                break

        from app.runner import build_and_upload_one_quant

        result = build_and_upload_one_quant(
            config,
            quant,
            Path(volume_root),
            force_hf_overwrite=force_hf_overwrite,
            already_published=already_published,
            api=hf_api,
        )
        build_status = result["status"]  # "uploaded" | "skipped_existing"
    except Exception as exc:  # noqa: BLE001 - report failure back, don't swallow
        build_status = "failed"
        error = str(exc)

    build_duration_s = time.monotonic() - started

    orchestrator_base_url = os.environ.get("ORCHESTRATOR_BASE_URL")
    callback_delivered = False
    callback_error = None
    if orchestrator_base_url and run_id is not None:
        import httpx
        from datetime import datetime, timezone

        payload = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
            "quant_builds": [
                {
                    "quant": quant,
                    "status": build_status,
                    "hf_repo_id": (result or {}).get("repo_id"),
                    "build_duration_s": build_duration_s,
                }
            ],
        }
        url = f"{orchestrator_base_url}/report/run/{run_id}"
        last_exc = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(url, json=payload)
                    response.raise_for_status()
                callback_delivered = True
                break
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        if not callback_delivered:
            # The run row would otherwise sit at "partial"/"running" forever
            # with no record of why — surface it in the returned payload so
            # whatever dispatched this job (and is watching job status) can
            # reconcile/retry the report instead of silently losing it.
            callback_error = str(last_exc)

    return {
        "config_stem": config_stem,
        "quant": quant,
        "status": build_status,
        "error": error,
        "callback_delivered": callback_delivered,
        "callback_error": callback_error,
    }


@runner_health
async def health_check(**_kwargs) -> dict:
    """Cheap post-deploy smoke test: proves this worker's environment can
    actually import mlx/mflux and see a CUDA-13 device, without running a
    multi-minute model build. Same Endpoint/worker pool/dependencies/env as
    run_generation, so a healthy response here is a faithful proxy for
    whether a real generation job would get past its import step.

    Dispatch after `flash deploy` (e.g. in CI) and fail the pipeline if
    status != "ok" — this is what would have caught the
    "libcublasLt.so.13: cannot open shared object file" failure without
    burning a full quant build to find it.

    Takes **_kwargs (ignored) because RunPod's queue worker SDK rejects a
    request with an empty `input` dict, so callers must send at least one
    key even though this check needs no parameters.
    """
    import ctypes
    import glob
    import os
    import site
    import subprocess
    import sys
    import time
    from pathlib import Path

    started = time.monotonic()
    try:
        _mflux_marker = Path("/tmp/.mflux_runner_deps_installed")
        if not _mflux_marker.exists():
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "install", "--quiet",
                    "mlx[cuda13]>=0.30.3,<0.32.0",
                    "mflux @ git+https://github.com/mflux-community/mflux.git",
                ],
                check=True,
            )
            _mflux_marker.touch()

        site_dirs = [*site.getsitepackages(), site.getusersitepackages()]
        nvidia_libs = [
            so
            for site_dir in site_dirs
            for so in glob.glob(os.path.join(site_dir, "nvidia", "*", "lib", "*.so*"))
        ]
        remaining = nvidia_libs
        for _ in range(2):
            still_failing = []
            for so in remaining:
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    still_failing.append(so)
            remaining = still_failing
            if not remaining:
                break

        import mlx.core as mx

        device = str(mx.default_device())
        hf_token_configured = os.environ.get("HF_TOKEN", "").startswith("hf_")

        return {
            "status": "ok",
            "mlx_device": device,
            "hf_token_configured": hf_token_configured,
            "nvidia_libs_loaded": len(nvidia_libs) - len(remaining),
            "nvidia_libs_failed": remaining,
            "duration_s": time.monotonic() - started,
        }
    except Exception as exc:  # noqa: BLE001 - report failure, don't crash the job
        return {
            "status": "failed",
            "error": str(exc),
            "duration_s": time.monotonic() - started,
        }
