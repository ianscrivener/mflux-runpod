"""/generate (PRD tasks 7, 8, 9).

Plans and records a generation run for a model series: resolves its config
(with request-param overrides), records a `runs` row, and hands off to the GPU
worker asynchronously via trigger_fn.

SAFETY: generate_one itself makes NO dispatch calls (no build/job creation)
— planning and DB bookkeeping only. trigger_fn defaults to a no-op dry-run
stub; real dispatch (dispatch_trigger, below) only runs when a caller
explicitly opts in with dispatch=true, so even a bare call with
HF_WORKER_URL set in the environment cannot accidentally start real, billed
GPU work.

Dispatch target: an HF Spaces Docker GPU worker (docker-runner-hf/, a
separate repo/deployment -- see its README.md and worker.py), replacing the
RunPod-based dispatch (per-series Network Volume + async job API on a
RunPod Serverless Docker Runner) removed while migrating off RunPod. No
ephemeral volume in the new design: the worker pulls source weights
straight from HF and pushes the finished quant straight back to HF.
"""

from datetime import datetime, timezone
from typing import Callable

from app.models_hf import load_models_hf
from app.models_missing import expected_repo_ids, load_configs
from app.report import create_run, finish_run

DEFAULT_MFLUX_REPO = "mflux (PyPI)"
DEFAULT_MFLUX_BRANCH = "main"
# mflux_repo/mflux_branch are recorded on the `runs` row for audit purposes
# only -- dispatch_trigger's HF Spaces worker installs a fixed mflux from
# PyPI at image-build time (see docker-runner-hf/Dockerfile) and has no
# per-job override mechanism, unlike the old RunPod Runner's
# force_mflux_repo (git+https://.../mflux.git@branch pip install at job
# time). mflux-community/mflux was suspended by GitHub 2026-08-20; PyPI is
# now the only supported install source project-wide (see pyproject.toml).


class UnknownModelError(ValueError):
    pass


class DispatchConfigError(RuntimeError):
    """Raised by dispatch_trigger/cancel_run when real GPU dispatch isn't
    available on this deployment (not configured, or -- currently -- not
    implemented at all) -- a deployment/config problem, not a bad request,
    but callers should still turn this into a clean error response instead
    of letting it surface as an unhandled 500."""

    pass


class InvalidQuantsError(ValueError):
    """Raised when a caller-supplied quant isn't one of the real quant
    names (q3/q4/q5/q6/q8/bf16) -- confirmed live 2026-08-20: a webapp form
    field with a plain "4" (not "q4") sailed straight through generate_one,
    got dispatched to the worker, and only failed there with a bare
    KeyError deep inside build_and_upload_one_quant, after real GPU time
    was already spent queuing it. Catching this here means a bad request
    fails fast, before dispatch_trigger ever runs."""

    pass


VALID_QUANTS = {"q3", "q4", "q5", "q6", "q8", "bf16"}


class InvalidHardwareError(ValueError):
    """Raised when a caller-supplied hardware tier id isn't one of HF Spaces'
    real tier names (huggingface_hub.SpaceHardware) -- same fail-fast intent
    as InvalidQuantsError, but here the stakes are higher: request_space_hardware
    restarts the Space, so a typo'd tier should never even reach that call."""

    pass


# Mirrors huggingface_hub.SpaceHardware's values exactly (confirmed live
# 2026-08-22) -- also the same 17 tier names in data-hf-sync/hf_hardware.json's
# "name" field. Spelled out here rather than importing SpaceHardware so this
# module doesn't need huggingface_hub at import time (matching the existing
# lazy-import pattern used for pause_worker/start_worker/etc, below).
VALID_HARDWARE_TIERS = {
    "cpu-basic", "cpu-upgrade", "zero-a10g",
    "t4-small", "t4-medium",
    "l4x1", "l4x4",
    "l40sx1", "l40sx4", "l40sx8",
    "a10g-small", "a10g-large", "a10g-largex2", "a10g-largex4",
    "a100-large", "a100x4", "a100x8",
}


def resolve_generate_config(
    model_stem: str,
    quants: list[str] | None = None,
    mflux_branch: str | None = None,
) -> dict:
    """Load configs/{model_stem}.yaml and apply request-param overrides
    (quants, mflux_branch) without mutating the file on disk."""
    configs = load_configs()
    if model_stem not in configs:
        raise UnknownModelError(f"no configs/{model_stem}.yaml found")

    if quants is not None:
        bad = [q for q in quants if q not in VALID_QUANTS]
        if bad:
            raise InvalidQuantsError(
                f"invalid quant(s) {bad} -- must be one of {sorted(VALID_QUANTS)} "
                "(e.g. 'q4', not '4')"
            )

    config = dict(configs[model_stem])
    if quants is not None:
        config["quants"] = quants
    config["mflux_branch"] = mflux_branch or DEFAULT_MFLUX_BRANCH
    return config


def dry_run_trigger(model_series: str, run_id: int, plan: dict) -> dict:
    """Default trigger_fn: plans and records only, fires nothing. Safe to call
    with no GPU worker configured — does not deploy or invoke anything."""
    return {"dispatched": False, "reason": "dry_run", "run_id": run_id, "plan": plan}


def dispatch_trigger(model_series: str, run_id: int, plan: dict) -> dict:
    """Real trigger_fn: POSTs one /build request per quant in
    plan["quants_to_build"] to the HF Spaces Docker GPU worker
    (docker-runner-hf/worker.py), each carrying run_id so the worker reports
    back through the durable HF bucket outbox (app.outbox.put_result),
    which this Orchestrator's own /outbox/poll then applies -- same delivery
    contract the old RunPod-based Runner used. Returns immediately once
    every quant is queued on the worker's side -- does not wait for a build
    to finish.

    Requires HF_WORKER_URL (the deployed Space's base URL, e.g.
    https://<user>-<space-name>.hf.space) in the Orchestrator's environment.

    Also requires HF_TOKEN if the Space is private (the normal case) --
    confirmed live 2026-08-20: a private Space's *.hf.space URL is gated by
    HF's own platform-level auth on the Authorization header, checked before
    the request ever reaches the container. Sending WORKER_API_KEY there
    instead (the original design) never got past that gate at all -- it came
    back as a masking 404, indistinguishable from "no such Space", not an
    auth error. HF_TOKEN goes on Authorization for that reason; WORKER_API_KEY
    (still optional, app-level, checked by worker.py itself) goes on a
    separate X-Worker-Api-Key header instead, since only one scheme can own
    Authorization on a private Space.
    """
    import os

    import httpx

    worker_url = os.environ.get("HF_WORKER_URL")
    if not worker_url:
        raise DispatchConfigError(
            "dispatch=true requires HF_WORKER_URL (the deployed HF Spaces "
            "GPU worker's base URL) in the Orchestrator's environment, but "
            "it's not set -- real GPU dispatch isn't configured on this "
            "deployment yet."
        )

    config = load_configs()[model_series]
    hf_token = os.environ.get("HF_TOKEN")
    headers = {"Authorization": f"Bearer {hf_token}"} if hf_token else {}
    worker_key = os.environ.get("WORKER_API_KEY")
    if worker_key:
        headers["X-Worker-Api-Key"] = worker_key

    queued = []
    with httpx.Client(timeout=30.0) as client:
        for quant in plan["quants_to_build"]:
            job = {
                "config_stem": model_series,
                "config": config,
                "quant": quant,
                "run_id": run_id,
                "force_hf_overwrite": plan["force_hf_overwrite"],
                # quants_to_build is already filtered to not-yet-published
                # quants (generate_one, below) -- anything dispatched here
                # is by construction not already published.
                "already_published": False,
            }
            try:
                response = client.post(f"{worker_url.rstrip('/')}/build", headers=headers, json=job)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                # A mid-loop failure used to propagate as a bare httpx
                # exception, silently dropping which quants (if any) had
                # already been queued on the worker's side before it -- the
                # caller (and the `runs` row) had no way to tell partial
                # dispatch from total failure. No retry/idempotency here
                # (that needs a worker-side dedup key, a real feature, not
                # a minimal fix) -- just stop and report accurately instead
                # of guessing whether it's safe to keep going.
                remaining = [q for q in plan["quants_to_build"] if q not in [j["quant"] for j in queued] + [quant]]
                raise DispatchConfigError(
                    f"dispatch failed on quant {quant!r} after successfully "
                    f"queuing {[j['quant'] for j in queued]} -- {exc}. "
                    f"{remaining} were never attempted."
                ) from exc
            queued.append({"quant": quant, **response.json()})

    return {"dispatched": True, "run_id": run_id, "jobs": queued}


# Space stages (huggingface_hub's SpaceStage) where the container isn't
# actually serving HTTP yet -- hitting /status against one of these would
# just time out into a 502 after several seconds for no new information, so
# worker_status() returns the stage directly instead of attempting it.
_SPACE_NOT_SERVING_STAGES = {
    "STOPPED",
    "PAUSED",
    "BUILDING",
    "APP_STARTING",
    "NO_APP_FILE",
    "CONFIG_ERROR",
    "BUILD_ERROR",
    "RUNTIME_ERROR",
    "DELETING",
}


def worker_status() -> dict:
    """GET the HF Spaces GPU worker's /status: current build state, queue
    depth, and in-flight prefetches (see docker-runner-hf/worker.py), plus
    the underlying Space's own runtime stage (SpaceStage -- RUNNING, PAUSED,
    STOPPED/asleep, APP_STARTING, etc, see docs/_HF_SPACE_COMMANDS.md) under
    "stage" and its current hardware tier (SpaceHardware, e.g. "a10g-large")
    under "hardware" -- lets the frontend's instance-size selector show what
    tier is actually running, not just what was last requested. Same
    HF_WORKER_URL/auth requirements as dispatch_trigger --
    raises DispatchConfigError if HF_WORKER_URL isn't set, and lets httpx
    errors (worker unreachable, e.g. its Space is asleep/restarting)
    propagate as-is so the caller can tell "not configured" from
    "configured but down".

    The stage lookup is best-effort: without HF_TOKEN, or if the lookup
    itself fails, this falls straight through to the plain HTTP /status call
    exactly as before "stage" existed -- pause_worker/start_worker still
    need HF_TOKEN regardless, this just degrades gracefully rather than
    turning a working status poll into a new failure mode."""
    import os

    import httpx

    worker_url = os.environ.get("HF_WORKER_URL")
    if not worker_url:
        raise DispatchConfigError(
            "HF_WORKER_URL (the deployed HF Spaces GPU worker's base URL) "
            "is not set on this deployment -- no worker to check status of."
        )

    hf_token = os.environ.get("HF_TOKEN")
    stage = None
    hardware = None
    if hf_token:
        from huggingface_hub import HfApi
        from huggingface_hub.errors import HFValidationError, OfflineModeIsEnabled

        try:
            runtime = HfApi(token=hf_token).get_space_runtime(_space_id())
            stage = runtime.stage
            hardware = runtime.hardware
        except (httpx.HTTPError, HFValidationError, OfflineModeIsEnabled):
            # Best-effort lookup (see docstring) -- HFValidationError is a
            # plain ValueError, not an httpx.HTTPError, and fires client-side
            # (no request sent at all) for a malformed HF_SPACE_ID (confirmed
            # live 2026-08-22: get_space_runtime("org/repo/bad slash") raises
            # it before touching the network). OfflineModeIsEnabled is an
            # OSError, not httpx.HTTPError either, and fires the same way
            # when HF_HUB_OFFLINE=1 is set. Neither was caught by the old
            # httpx.HTTPError-only except, so either would have crashed this
            # whole best-effort lookup instead of falling through to the
            # plain /status call below.
            pass

    if stage in _SPACE_NOT_SERVING_STAGES:
        return {
            "stage": stage,
            "hardware": hardware,
            "state": None,
            "queue_depth": None,
            "prefetching": [],
            "current": None,
            "last_result": None,
        }

    headers = {"Authorization": f"Bearer {hf_token}"} if hf_token else {}
    worker_key = os.environ.get("WORKER_API_KEY")
    if worker_key:
        headers["X-Worker-Api-Key"] = worker_key

    with httpx.Client(timeout=15.0) as client:
        response = client.get(f"{worker_url.rstrip('/')}/status", headers=headers)
        response.raise_for_status()
        result = response.json()
        result["stage"] = stage
        result["hardware"] = hardware
        return result


# The Space this worker runs on -- distinct from HF_WORKER_URL (its runtime
# *.hf.space URL, used above for /build and /status) and from
# app.outbox.DEFAULT_BUCKET_ID (its companion Persistent Storage bucket, a
# separate HF repo). Overridable via HF_SPACE_ID per the project's existing
# pattern (see app.outbox.OUTBOX_BUCKET_ID) rather than derived from
# HF_WORKER_URL's hostname, which isn't reliably reversible for orgs whose
# name itself contains a hyphen.
DEFAULT_SPACE_ID = "cleverheart2026/mflux-model-gpu-runner"


def _space_id() -> str:
    import os

    return os.environ.get("HF_SPACE_ID", DEFAULT_SPACE_ID)


def _require_hf_token() -> str:
    import os

    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise DispatchConfigError(
            "this action requires HF_TOKEN (the credential used to manage "
            "the GPU worker's own HF Space) in the Orchestrator's "
            "environment, but it's not set."
        )
    return hf_token


def pause_worker() -> dict:
    """Pause the GPU worker's underlying HF Space (huggingface_hub's
    pause_space -- see docs/_HF_SPACE_COMMANDS.md). Distinct from the
    automatic "sleep" a free/idle Space enters on its own: pause is
    explicit and stays paused until start_worker() (restart_space) brings
    it back. Either way, per docs/v0.2.0/hf-space-sleep-clears-cache.md,
    resuming means a full container rebuild -- local disk (including the
    HF Hub download cache) does not survive."""
    from huggingface_hub import HfApi

    hf_token = _require_hf_token()
    runtime = HfApi(token=hf_token).pause_space(_space_id())
    return {"stage": runtime.stage, "hardware": runtime.hardware}


def start_worker() -> dict:
    """Bring the GPU worker's HF Space back up (huggingface_hub's
    restart_space -- the same call whether the Space is currently paused,
    asleep, or just needs a fresh build; restart_space is documented as
    "the only way to programmatically restart a Space if you've put it on
    Pause", see docs/_HF_SPACE_COMMANDS.md)."""
    from huggingface_hub import HfApi

    hf_token = _require_hf_token()
    runtime = HfApi(token=hf_token).restart_space(_space_id())
    return {"stage": runtime.stage, "hardware": runtime.hardware}


def set_worker_hardware(hardware: str) -> dict:
    """Change the GPU worker's HF Space hardware tier (huggingface_hub's
    request_space_hardware -- see docs/_HF_SPACE_COMMANDS.md). Requesting a
    new tier RESTARTS the Space to apply it (same container-rebuild/cache-
    loss caveat as pause_worker/start_worker, see
    docs/v0.2.0/hf-space-sleep-clears-cache.md) -- callers must only call
    this when nothing is currently building on the worker."""
    from huggingface_hub import HfApi

    if hardware not in VALID_HARDWARE_TIERS:
        raise InvalidHardwareError(
            f"invalid hardware tier {hardware!r} -- must be one of {sorted(VALID_HARDWARE_TIERS)}"
        )

    hf_token = _require_hf_token()
    runtime = HfApi(token=hf_token).request_space_hardware(_space_id(), hardware=hardware)
    return {"stage": runtime.stage, "hardware": runtime.hardware}


# fetch_space_logs(..., follow=False) is non-blocking and returns whatever's
# currently buffered server-side (same as `docker logs`, not `docker logs
# -f`) -- fine for a plain request/response GET, no streaming/SSE needed on
# this side. Capped rather than returned in full: HF buffers a long scrollback
# for a long-running Space, and the frontend only ever shows the tail of it.
MAX_LOG_LINES = 500


def fetch_worker_logs(build: bool) -> dict:
    """Fetch the GPU worker Space's build logs (build=True, the container
    image build -- useful when it's stuck in BUILD_ERROR) or run logs
    (build=False, the running app's stdout/stderr -- see
    docker-runner-hf/worker.py). Same HF_TOKEN requirement as pause/start."""
    from huggingface_hub import HfApi

    hf_token = _require_hf_token()
    lines = list(HfApi(token=hf_token).fetch_space_logs(_space_id(), build=build, follow=False))
    return {"lines": [line.rstrip("\n") for line in lines[-MAX_LOG_LINES:]]}


def cancel_run(run_id: int) -> dict:
    """Cancel every still-in-flight job for a run and mark it cancelled.

    Not implemented. dispatch_trigger can now dispatch real work, but the HF
    Spaces worker (docker-runner-hf/worker.py) has no cancel endpoint --
    it's a plain FIFO queue with no per-job ids exposed, and aborting a
    quant mid-build isn't something to bolt on without deciding what state
    that leaves the worker's local build directory in. Kept as a stub
    (rather than deleted) so /generate/{run_id}/cancel has a clean seam to
    wire up once that's actually designed."""
    from app.report import run_detail

    if run_detail(run_id) is None:
        raise UnknownModelError(f"run {run_id} not found")

    raise DispatchConfigError(
        "cancel is not available on this deployment -- the HF Spaces GPU "
        "worker has no cancel endpoint yet."
    )


def generate_one(
    model_stem: str,
    quants: list[str] | None = None,
    mflux_repo: str | None = None,
    mflux_branch: str | None = None,
    force_hf_overwrite: bool = False,
    trigger_fn: Callable[[str, int, dict], dict] = dry_run_trigger,
) -> dict:
    """Plan+record a single model's generation run: writes a `runs` row and
    calls trigger_fn (a no-op dry-run by default) to hand off to the GPU
    worker. Makes no dispatch calls itself — that's trigger_fn's
    responsibility once a real one exists. Returns the trigger_fn result plus
    the run plan."""
    config = resolve_generate_config(model_stem, quants=quants, mflux_branch=mflux_branch)
    mflux_repo = mflux_repo or DEFAULT_MFLUX_REPO

    hf_manifest = load_models_hf()
    repo_ids = expected_repo_ids(config)
    published = {m["model_name"] for m in hf_manifest.get("hf_models", [])}
    quants_to_build = [
        q for q, repo_id in repo_ids.items() if force_hf_overwrite or repo_id not in published
    ]

    started_at = datetime.now(timezone.utc)
    run_id = create_run(
        model_series=model_stem,
        started_at=started_at.isoformat(),
        expected_quants=len(quants_to_build),
        quants=quants_to_build,
        hf_model_name=config.get("hf_model_name"),
        mflux_repo=mflux_repo,
        mflux_branch=config["mflux_branch"],
        force_hf_overwrite=force_hf_overwrite,
    )

    if not quants_to_build:
        # Nothing to do (every quant already published, or none declared) --
        # expected_quants=0 means update_run_status_from_children never gets
        # called for this run_id (it's only invoked by a quant job's
        # callback, and no quant jobs are ever dispatched), so without this
        # the run sits at its 'running' default forever. Confirmed live,
        # 2026-08-18: several runs stuck exactly this way.
        finished_at = datetime.now(timezone.utc)
        finish_run(
            run_id,
            finished_at=finished_at.isoformat(),
            duration_s=(finished_at - started_at).total_seconds(),
            status="success",
        )

    plan = {
        "model_stem": model_stem,
        "hf_model_name": config.get("hf_model_name"),
        "mflux_repo": mflux_repo,
        "mflux_branch": config["mflux_branch"],
        "force_hf_overwrite": force_hf_overwrite,
        "quants_to_build": quants_to_build,
    }

    try:
        dispatch = trigger_fn(model_stem, run_id, plan)
    except DispatchConfigError as exc:
        # trigger_fn (dispatch_trigger) already created the `runs` row above
        # before it could fail -- without this, a dispatch failure (worker
        # unreachable, mid-loop POST failure, etc.) propagates straight out
        # of /generate as a 503 while the run row is left at its 'running'
        # default forever, with no visible error. exc's message already
        # carries which quants were successfully queued vs. never attempted
        # (see dispatch_trigger) -- preserved here as the run's error detail.
        finished_at = datetime.now(timezone.utc)
        finish_run(
            run_id,
            finished_at=finished_at.isoformat(),
            duration_s=(finished_at - started_at).total_seconds(),
            status="failed",
            error=str(exc),
        )
        raise
    return {"run_id": run_id, "plan": plan, "dispatch": dispatch}
