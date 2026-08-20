"""/generate (PRD tasks 7, 8, 9).

Plans and records a generation run for a model series: resolves its config
(with request-param overrides), records a `runs` row, and hands off to the GPU
worker asynchronously via trigger_fn.

SAFETY: there is no dispatch mechanism wired up right now -- the RunPod-based
one (network volume + async job API) was removed while migrating to a
Hugging Face Spaces Docker worker, and this module must never silently start
real, billed GPU work in the meantime. generate_one itself makes NO dispatch
calls (no volume/job creation) — planning and DB bookkeeping only. trigger_fn
defaults to a no-op dry-run stub. Real dispatch belongs inside a real
trigger_fn once a live worker exists and its invocation contract is decided
with the user — NOT in generate_one, so that even a bare call cannot
accidentally provision paid infrastructure.
"""

from datetime import datetime, timezone
from typing import Callable

from app.models_hf import load_models_hf
from app.models_missing import expected_repo_ids, load_configs
from app.report import create_run, finish_run

DEFAULT_MFLUX_REPO = "https://github.com/mflux-community/mflux.git"
DEFAULT_MFLUX_BRANCH = "main"


class UnknownModelError(ValueError):
    pass


class DispatchConfigError(RuntimeError):
    """Raised by dispatch_trigger/cancel_run when real GPU dispatch isn't
    available on this deployment (not configured, or -- currently -- not
    implemented at all) -- a deployment/config problem, not a bad request,
    but callers should still turn this into a clean error response instead
    of letting it surface as an unhandled 500."""

    pass


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
    """Real trigger_fn: hands the plan off to the GPU worker to build every
    quant in plan["quants_to_build"], each carrying run_id so it reports back
    via POST /report/run/{run_id}.

    Not implemented. The previous implementation dispatched an async RunPod
    job per quant (network volume + Docker Runner on RunPod's serverless
    platform); that was removed while migrating to a Hugging Face Spaces
    Docker worker (pull source weights from HF, quantize, push the result
    back to HF — no ephemeral volume). dry_run_trigger remains safe to use
    until a real trigger_fn is wired up for the new worker.
    """
    raise DispatchConfigError(
        "dispatch=true is not available on this deployment -- real GPU "
        "dispatch was removed while migrating off RunPod and the "
        "replacement (HF Spaces Docker worker) dispatch mechanism isn't "
        "wired up yet. Use dispatch=false (the default) for planning/"
        "dry-run only."
    )


def cancel_run(run_id: int) -> dict:
    """Cancel every still-in-flight job for a run and mark it cancelled.

    Not implemented, for the same reason as dispatch_trigger: no dispatch
    mechanism currently exists, so there is nothing live to cancel. Kept as
    a stub (rather than deleted) so /generate/{run_id}/cancel has the same
    clean seam to wire up again once a real worker exists."""
    from app.report import run_detail

    if run_detail(run_id) is None:
        raise UnknownModelError(f"run {run_id} not found")

    raise DispatchConfigError(
        "cancel is not available on this deployment -- real GPU dispatch "
        "(and therefore cancellation) was removed while migrating off "
        "RunPod and the replacement worker isn't wired up yet."
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

    dispatch = trigger_fn(model_stem, run_id, plan)
    return {"run_id": run_id, "plan": plan, "dispatch": dispatch}
