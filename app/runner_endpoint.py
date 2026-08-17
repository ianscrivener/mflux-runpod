"""GPU Runner Flash @Endpoint (PRD: (2) Runner - GPU; task 14).

Wraps app/runner.py's build_and_upload_one_quant() as a Flash serverless
endpoint on CUDA 13, matching the mflux/mlx CUDA-13 preference.

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
constant the handler needs is defined *inside* run_generation().

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

runner = Endpoint(
    name="mflux-runner",
    gpu=GpuGroup.ADA_24,  # RTX 4090 tier; matches runpod.yaml's gpu_type_std
    min_cuda_version=CudaVersion.V13_0,  # mflux/mlx has a hard CUDA 13 preference
    workers=(0, 1),  # one at a time while testing; raise once the pipeline is proven
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
        # shared object file") unless LD_LIBRARY_PATH points at them first.
        # Must happen before `from app.runner import ...` pulls in mflux/mlx.
        import glob
        import site

        site_dirs = [*site.getsitepackages(), site.getusersitepackages()]
        nvidia_lib_dirs = [
            d
            for site_dir in site_dirs
            for d in glob.glob(os.path.join(site_dir, "nvidia", "*", "lib"))
        ]
        if nvidia_lib_dirs:
            os.environ["LD_LIBRARY_PATH"] = ":".join(
                nvidia_lib_dirs + [os.environ.get("LD_LIBRARY_PATH", "")]
            )

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
