"""RunPod Serverless handler for the GPU Runner Docker image (dockerFiles/runner.dockerfile).

Replaces app/runner_endpoint.py's Flash @Endpoint wrapper for the case where
the Runner is deployed as a plain Docker image instead of via `flash deploy`.
app/runner.py's actual logic (build_and_upload_one_quant, find_model_class,
etc.) is unchanged and shared between both deployment paths -- only this
entrypoint differs.

mlx/mflux are installed here, at container START (via `uv pip`, already in
the image's venv), not baked into dockerFiles/runner.dockerfile -- that image
only carries CUDA/cuDNN/Python/system libs, which rarely change. By default,
this installs the current mlx[cuda13] + mflux-community/mflux@main. Both the
repo URL and branch can be overridden per-job (a fork/PR someone wants to
test, e.g. one adding a new model) without rebuilding the image at all --
when a custom repo/branch is requested, the default mflux install is
uninstalled first, then the custom one installed on top (see
_ensure_mflux_installed).

Job input shape (event["input"]):
  {
    "config_stem": str,
    "config": dict,          # resolved configs/{config_stem}.yaml, as data
    "quant": str,
    "volume_root": str,      # local path to the mounted per-series volume
    "mflux_repo_url": str,   # optional, default the mflux-community repo
    "mflux_branch": str,     # optional, default "main"
    "force_hf_overwrite": bool,   # optional, default False
    "already_published": bool,    # optional, default False
    "run_id": int | None,         # optional, enables the Orchestrator callback
  }

Environment (set at container/endpoint deploy time, not per-job -- same
SSRF/credential-leak reasoning as app/runner_endpoint.py's docstring):
  HF_TOKEN              RunPod Secret
  ORCHESTRATOR_BASE_URL Orchestrator's base URL, for the /report/run/{id} callback
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import runpod
from huggingface_hub import HfApi

_MFLUX_MARKER = Path("/tmp/.mflux_runner_deps_installed")
DEFAULT_MFLUX_REPO_URL = "https://github.com/mflux-community/mflux.git"


MLX_VERSION_RANGE = "mlx[cuda13]>=0.30.3,<0.32.0"
# NOT a staleness pin -- a correctness constraint from mflux's own
# pyproject.toml (mlx<0.32.0 on Linux has a known quantized_matmul bug per
# mflux's own upstream comment). Every mflux branch/fork should still want
# this same range; if a future branch needs a different mlx range, that
# becomes a job input too.

DEFAULT_TARGET = f"{DEFAULT_MFLUX_REPO_URL}@main"


def _ensure_mflux_installed(mflux_repo_url: str, mflux_branch: str) -> None:
    """Installs the current mlx[cuda13] + mflux (mflux-community@main by
    default), once per warm container. Guarded by a marker file so a warm
    container only pays this cost once.

    A custom repo_url/branch (a fork/PR someone wants to test) is handled
    differently: the default mflux install is uninstalled first, then the
    custom one installed on top -- a straight `uv pip install` over an
    existing git-source package doesn't reliably swap the source, since pip
    resolvers can treat an already-satisfied requirement as a no-op. This
    only runs once per (repo_url, branch) pair per warm container -- RunPod's
    scheduler gives no affinity guarantee between a worker and a job's
    parameters, so a later job on the same warm worker asking for a
    different branch correctly re-triggers the swap rather than silently
    keeping a previous job's custom mflux.
    """
    target = f"{mflux_repo_url}@{mflux_branch}"
    if _MFLUX_MARKER.exists() and _MFLUX_MARKER.read_text() == target:
        return

    # Bounded well under RunPod's own job deadline -- a hung network/git
    # fetch here would otherwise block indefinitely inside handler()'s try
    # block, past the job's real timeout, without ever reaching the
    # exception handler that reports a clean failure back to the Orchestrator.
    install_timeout_s = 300

    if target == DEFAULT_TARGET:
        subprocess.run(
            ["uv", "pip", "install", "--quiet", MLX_VERSION_RANGE,
             f"mflux @ git+{mflux_repo_url}@{mflux_branch}"],
            check=True, timeout=install_timeout_s,
        )
    else:
        # Custom branch/fork requested -- uninstall whatever mflux is
        # currently present (the default, or a different custom branch from
        # an earlier job on this same warm worker) before installing the
        # requested one.
        subprocess.run(
            ["uv", "pip", "uninstall", "--quiet", "mflux"],
            check=False, timeout=60,
        )
        subprocess.run(
            ["uv", "pip", "install", "--quiet", MLX_VERSION_RANGE,
             f"mflux @ git+{mflux_repo_url}@{mflux_branch}"],
            check=True, timeout=install_timeout_s,
        )

    _MFLUX_MARKER.write_text(target)


def handler(event: dict) -> dict:
    job_input = event.get("input") or {}

    # run_id read first (with a safe default) so a malformed job -- e.g.
    # missing a required field below -- can still report a structured
    # failure back to the Orchestrator instead of crashing the worker
    # process with an unhandled KeyError before the callback URL/run_id are
    # even known.
    run_id = job_input.get("run_id")
    quant = job_input.get("quant")
    config_stem = job_input.get("config_stem")

    hf_api = HfApi(token=os.environ.get("HF_TOKEN"))

    started = time.monotonic()
    error = None
    result = None
    try:
        # Required-field validation lives inside the try so a missing field
        # is reported as a normal build failure (structured payload +
        # Orchestrator callback below), not an unhandled crash.
        config = job_input["config"]
        volume_root = job_input["volume_root"]
        if quant is None or config_stem is None:
            raise KeyError("config_stem and quant are required job input fields")
        mflux_repo_url = job_input.get("mflux_repo_url", DEFAULT_MFLUX_REPO_URL)
        mflux_branch = job_input.get("mflux_branch", "main")
        force_hf_overwrite = job_input.get("force_hf_overwrite", False)
        already_published = job_input.get("already_published", False)

        _ensure_mflux_installed(mflux_repo_url, mflux_branch)

        from app.runner import build_and_upload_one_quant

        result = build_and_upload_one_quant(
            config,
            quant,
            Path(volume_root),
            force_hf_overwrite=force_hf_overwrite,
            already_published=already_published,
            api=hf_api,
        )
        build_status = result["status"]
    except Exception as exc:  # noqa: BLE001 - report failure back, don't swallow
        build_status = "failed"
        error = str(exc)

    build_duration_s = time.monotonic() - started

    orchestrator_base_url = os.environ.get("ORCHESTRATOR_BASE_URL")
    callback_delivered = False
    callback_error = None
    if orchestrator_base_url and run_id is not None:
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
                    # Flash LB routes take the handler's arg wrapped in
                    # {"data": ...} -- confirmed live against
                    # orchestrator_endpoint.py's report_run_callback.
                    response = client.post(url, json={"data": payload})
                    response.raise_for_status()
                callback_delivered = True
                break
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        if not callback_delivered:
            callback_error = str(last_exc)

    return {
        "config_stem": config_stem,
        "quant": quant,
        "status": build_status,
        "error": error,
        "callback_delivered": callback_delivered,
        "callback_error": callback_error,
    }


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
