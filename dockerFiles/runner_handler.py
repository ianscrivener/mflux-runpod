"""RunPod Serverless handler for the GPU Runner Docker image (dockerFiles/runner.dockerfile).

Replaces app/runner_endpoint.py's Flash @Endpoint wrapper for the case where
the Runner is deployed as a plain Docker image instead of via `flash deploy`.
app/runner.py's actual logic (build_and_upload_one_quant, find_model_class,
etc.) is unchanged and shared between both deployment paths -- only this
entrypoint differs.

mlx/mflux are baked into dockerFiles/runner.dockerfile at IMAGE BUILD time
(see BAKED_MLX_VERSION / BAKED_MFLUX_TARGET below, which must match the
Dockerfile's install lines). By default this handler installs NOTHING at
container start -- it just uses what's in the image. force_mlx_ver and
force_mflux_repo are opt-in per-job overrides (e.g. testing a different mlx
release, or a fork/branch of mflux) that pip-install on top of the baked
image without rebuilding it; when neither is set, no pip install runs at
all (see _apply_overrides).

Job input shape (event["input"]):
  {
    "config_stem": str,
    "config": dict,          # resolved configs/{config_stem}.yaml, as data
    "quant": str,
    "volume_root": str,      # local path to the mounted per-series volume
    "force_mlx_ver": str,       # optional, e.g. "0.35.0" -- overrides the baked mlx
    "force_mflux_repo": str,    # optional, "https://.../mflux.git@branch" -- overrides the baked mflux
    "force_hf_overwrite": bool,   # optional, default False
    "already_published": bool,    # optional, default False
    "run_id": int | None,         # optional, enables the DO Spaces outbox result delivery
  }

Environment (set at container/endpoint deploy time, not per-job -- same
SSRF/credential-leak reasoning as app/runner_endpoint.py's docstring):
  HF_TOKEN                                          RunPod Secret
  DO_SPACES_KEY/SECRET/ENDPOINT/REGION/BUCKET        DO Spaces outbox creds (see app/outbox.py)
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import runpod
from huggingface_hub import HfApi

_OVERRIDE_MARKER = Path("/tmp/.mflux_runner_overrides_applied")

# Must match dockerFiles/runner.dockerfile's baked-in install lines exactly.
# Only used to *restore* a warm container to the baked state after a
# previous job on it applied an override (see _apply_overrides) -- the
# default (no-override) path never reads these to install anything, since
# the image already has them.
BAKED_MLX_VERSION = "0.32.0"
# NOTE: mflux's own pyproject.toml normally pins mlx<0.32.0 to work around a
# known CUDA/Linux quantized_matmul bug in mlx>=0.32.0. 0.32.0 is baked in
# here anyway per explicit request; quantized (non-bf16) builds against the
# baked default may hit that bug until force_mlx_ver pins back below 0.32.0.
BAKED_MFLUX_TARGET = "https://github.com/mflux-community/mflux.git@main"


def _apply_overrides(force_mlx_ver: str | None, force_mflux_repo: str | None) -> None:
    """No-op by default -- the image already has a working mlx+mflux baked
    in (dockerFiles/runner.dockerfile), so when neither override is set this
    installs nothing at all.

    force_mlx_ver pip-installs a specific mlx[cuda13] version on top of the
    image. force_mflux_repo (format: "https://.../mflux.git@branch", pip's
    own git-VCS syntax) uninstalls the baked mflux and installs that
    repo/branch instead -- a straight `uv pip install` over an existing
    git-source package doesn't reliably swap the source, since pip resolvers
    can treat an already-satisfied requirement as a no-op.

    Guarded by a marker file so a warm container only pays the install cost
    once per requested state. RunPod's scheduler gives no affinity guarantee
    between a worker and a job's parameters, so a later job on the same warm
    worker asking for a *different* state (including reverting to no
    override) correctly re-triggers a swap back to the baked default rather
    than silently keeping a previous job's override.
    """
    state = f"mlx={force_mlx_ver or 'baked'}|mflux={force_mflux_repo or 'baked'}"
    if _OVERRIDE_MARKER.exists() and _OVERRIDE_MARKER.read_text() == state:
        return

    previously_overridden = _OVERRIDE_MARKER.exists()

    # Bounded well under RunPod's own job deadline -- a hung network/git
    # fetch here would otherwise block indefinitely inside handler()'s try
    # block, past the job's real timeout, without ever reaching the
    # exception handler that reports a clean failure back to the Orchestrator.
    install_timeout_s = 300

    if force_mlx_ver:
        subprocess.run(
            ["uv", "pip", "install", "--quiet", f"mlx[cuda13]=={force_mlx_ver}"],
            check=True, timeout=install_timeout_s,
        )
    elif previously_overridden:
        subprocess.run(
            ["uv", "pip", "install", "--quiet", f"mlx[cuda13]=={BAKED_MLX_VERSION}"],
            check=True, timeout=install_timeout_s,
        )

    if force_mflux_repo:
        subprocess.run(
            ["uv", "pip", "uninstall", "--quiet", "mflux"],
            check=False, timeout=60,
        )
        subprocess.run(
            ["uv", "pip", "install", "--quiet", f"mflux @ git+{force_mflux_repo}"],
            check=True, timeout=install_timeout_s,
        )
    elif previously_overridden:
        subprocess.run(
            ["uv", "pip", "uninstall", "--quiet", "mflux"],
            check=False, timeout=60,
        )
        subprocess.run(
            ["uv", "pip", "install", "--quiet", f"mflux @ git+{BAKED_MFLUX_TARGET}"],
            check=True, timeout=install_timeout_s,
        )

    _OVERRIDE_MARKER.write_text(state)


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
        force_mlx_ver = job_input.get("force_mlx_ver")
        force_mflux_repo = job_input.get("force_mflux_repo")
        force_hf_overwrite = job_input.get("force_hf_overwrite", False)
        already_published = job_input.get("already_published", False)

        _apply_overrides(force_mlx_ver, force_mflux_repo)

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

    # Durable outbox (DO Spaces) instead of a direct HTTP callback -- the
    # Orchestrator polls this on its own schedule, so it doesn't need to be
    # reachable at the exact moment this job finishes (2026-08-18 design
    # change: the old direct-POST callback required the Orchestrator to be
    # online right now, which doesn't hold if it's ever run somewhere that
    # isn't always-on). See app/outbox.py.
    outbox_delivered = False
    outbox_error = None
    if run_id is not None:
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
        try:
            from app.outbox import put_result

            put_result(run_id, quant, payload)
            outbox_delivered = True
        except Exception as exc:  # noqa: BLE001 - report failure back, don't crash the job
            outbox_error = str(exc)

    return {
        "config_stem": config_stem,
        "quant": quant,
        "status": build_status,
        "error": error,
        "outbox_delivered": outbox_delivered,
        "outbox_error": outbox_error,
    }


if __name__ == "__main__":
    # Logged before start() blocks, so "did the handler process actually
    # come up" is answerable from the worker's container logs. A worker that
    # reports "ready" (RunPod's own agent message) but never logs this line
    # means the container started but this script didn't -- the exact
    # silent-failure mode that made an earlier broken image hard to diagnose.
    print("mflux runner_handler starting, registering with runpod.serverless", flush=True)
    runpod.serverless.start({"handler": handler})
