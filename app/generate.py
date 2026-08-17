"""/generate, /generate_all (PRD tasks 7, 8, 9).

Plans and records a generation run for a model series: resolves its config
(with request-param overrides), records a `runs` row, and hands off to the GPU
Runner asynchronously via trigger_fn.

SAFETY: there is no @Endpoint-deployed Runner yet (see ToDo.md task 14/15), and
this module must never silently start real, billed RunPod work. generate_one/
generate_all themselves make NO RunPod API calls (no volume creation, no GPU
dispatch) — planning and DB bookkeeping only. trigger_fn defaults to a no-op
dry-run stub. Volume creation belongs inside a real trigger_fn once a live
Runner endpoint exists and its invocation contract is decided with the user —
NOT in generate_one, so that even a bare call with RUNPOD_API_KEY set in the
environment cannot provision paid storage.
"""

from datetime import datetime, timezone
from typing import Callable

from app.models_hf import load_models_hf
from app.models_missing import expected_repo_ids, load_configs, load_overrides
from app.report import create_run

DEFAULT_MFLUX_REPO = "https://github.com/mflux-community/mflux.git"
DEFAULT_MFLUX_BRANCH = "main"


class UnknownModelError(ValueError):
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
    with no RunPod GPU authorization — does not deploy or invoke a Runner."""
    return {"dispatched": False, "reason": "dry_run", "run_id": run_id, "plan": plan}


def generate_one(
    model_stem: str,
    quants: list[str] | None = None,
    mflux_repo: str | None = None,
    mflux_branch: str | None = None,
    force_hf_overwrite: bool = False,
    trigger_fn: Callable[[str, int, dict], dict] = dry_run_trigger,
) -> dict:
    """Plan+record a single model's generation run: writes a `runs` row and
    calls trigger_fn (a no-op dry-run by default) to hand off to the Runner.
    Makes no RunPod API calls itself — volume creation is trigger_fn's
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

    run_id = create_run(
        model_series=model_stem,
        started_at=datetime.now(timezone.utc).isoformat(),
        expected_quants=len(quants_to_build),
        hf_model_name=config.get("hf_model_name"),
        mflux_repo=mflux_repo,
        mflux_branch=config["mflux_branch"],
        force_hf_overwrite=force_hf_overwrite,
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


def generate_all(
    trigger_fn: Callable[[str, int, dict], dict] = dry_run_trigger,
) -> list[dict]:
    """Plan+record a generation run for every series /models_missing reports."""
    from app.models_missing import compute_missing

    configs = load_configs()
    hf_manifest = load_models_hf()
    overrides = load_overrides()
    missing = compute_missing(configs, hf_manifest, overrides)["missing"]

    return [
        generate_one(model_stem, trigger_fn=trigger_fn) for model_stem in missing
    ]
