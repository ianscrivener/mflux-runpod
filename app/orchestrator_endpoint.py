"""Orchestrator Flash @Endpoint (PRD: (1) Orchestrator - CPU).

Load-balanced (Mode 2) Flash routes wrapping app/main.py's FastAPI logic --
each route here just calls the same app/*.py functions app/main.py's routes
call, so this stays a thin transport layer rather than a second copy of the
Orchestrator's logic.

REPORT_DB_PATH must point at a mounted NetworkVolume path, not local
container disk -- verified live (2026-08-17) that `volume=NetworkVolume(...)`
on a Flash Endpoint mounts at /runpod-volume, AND that data written there
survives a worker scaling to zero (idle_timeout=60s): a run row written at
08:11 UTC was still readable an hour later, across multiple worker restarts
and a full redeploy in between.

Pinned to EU-RO-1 -- NOT the same datacenter as mflux-runner/
mflux-runner-health (US-IL-1) -- because Flash's CPU/load-balanced
endpoints only work in EU-RO-1 (runpod_flash.core.resources.datacenter.
CPU_DATACENTERS is hardcoded to exactly that one datacenter; confirmed live,
2026-08-17, via a real `flash deploy` failure: "CPU endpoints are not
available in: US-IL-1. Supported CPU data centers: EU-RO-1"). This is a
Flash-specific constraint, separate from which datacenters support network
volumes/S3 or GPU capacity -- the Orchestrator and the Runner endpoints
don't need to share a datacenter, since each only mounts its own volume.

`mflux-orchestrator` (this Endpoint's NetworkVolume) is a dedicated volume
in EU-RO-1, not shared with the Runner's US-IL-1 volume(s) -- network
volumes are datacenter-pinned on RunPod, not truly cross-region (confirmed
against runpod_flash's own NetworkVolume/serverless resource code and
RunPod's docs, 2026-08-17).

Per the Flash skill's Gotcha #1 (only the function body ships to `flash dev`
workers), every import each route needs is defined inside that route's own
body, matching the pattern in app/runner_endpoint.py.

NOT deployed by default -- this module only defines the endpoint. Deploying
it (`flash deploy`) or running it against `flash dev` provisions/uses real,
billed RunPod CPU workers + a network volume. Do that deliberately.
"""

from pydantic import BaseModel, Field
from runpod_flash import CpuInstanceType, DataCenter, Endpoint, NetworkVolume

ORCHESTRATOR_DATACENTER = DataCenter.EU_RO_1  # Flash CPU endpoints: EU-RO-1 only

# Module-level, not inside the route body like Gotcha #1 normally requires --
# Flash's LB handler wrapper (runpod_flash.runtime.lb_handler) special-cases
# a handler whose body param is already a Pydantic BaseModel: it's passed
# through unwrapped instead of being nested under a synthesized {"data": ...}
# key (see dockerFiles/runner_handler.py's callback, which DOES need that
# wrapping because it targets data: dict there). That only works if the
# model is a real class FastAPI can introspect for its JSON schema -- which
# requires it to exist as a class, not be reconstructed inside each request.
# Verified via `flash build` (build-only, no deploy) that this ships correctly
# in the generated worker handler before ever touching the live endpoint.
class GenerateRequest(BaseModel):
    config_stem: str = Field(
        ...,
        description="Model series to generate, matching a configs/{config_stem}.yaml "
        "file exactly (see GET /models_supported or /models_missing for valid values). "
        "Case-sensitive filename stem, not the Hugging Face model name.",
        examples=["Fibo"],
    )
    quants: list[str] | None = Field(
        default=None,
        description="Which quantizations to build, e.g. [\"q4\", \"q8\"]. Omit to build "
        "every quant declared in the config, minus any already published on Hugging "
        "Face (see /models_hf) unless force_hf_overwrite is set.",
        examples=[["q4", "q8"]],
    )
    mflux_repo: str | None = Field(
        default=None,
        description="Override the mflux source repo to build against (a fork/PR under "
        "test). Omit to use the default (mflux-community/mflux). Only takes effect "
        "when dispatch=true.",
        examples=["https://github.com/mflux-community/mflux.git"],
    )
    mflux_branch: str | None = Field(
        default=None,
        description="Branch of mflux_repo to build against. Omit for \"main\". Only "
        "takes effect when dispatch=true.",
        examples=["main"],
    )
    force_hf_overwrite: bool = Field(
        default=False,
        description="Rebuild and overwrite a quant even if it's already published on "
        "Hugging Face, instead of skipping it.",
    )
    dispatch: bool = Field(
        default=False,
        description="Opt-in to real work. false (default) = dry-run: plans and records "
        "a `runs` row only, makes no RunPod API calls, dispatches nothing. true = "
        "creates/reuses the series' RunPod network volume and dispatches one real, "
        "billed GPU job per quant to the Runner (see app/generate.py::dispatch_trigger).",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "config_stem": "Fibo",
                    "quants": ["q4", "q8"],
                    "force_hf_overwrite": False,
                    "dispatch": False,
                }
            ]
        }
    }


class GenerateAllRequest(BaseModel):
    dispatch: bool = Field(
        default=False,
        description="Same opt-in as /generate's dispatch field, applied to every "
        "series GET /models_missing currently reports as missing at least one quant. "
        "false (default) dry-runs all of them; true dispatches a real GPU job per "
        "missing quant, across every missing series, in one call.",
    )

    model_config = {"json_schema_extra": {"examples": [{"dispatch": False}]}}

orchestrator = Endpoint(
    name="mflux-orchestrator",
    cpu=CpuInstanceType.CPU3C_1_2,
    workers=(0, 1),
    idle_timeout=60,
    datacenter=ORCHESTRATOR_DATACENTER,
    dependencies=["pyyaml", "httpx", "boto3"],
    volume=NetworkVolume(name="mflux-orchestrator", datacenter=ORCHESTRATOR_DATACENTER, size=10),
    env={
        "REPORT_DB_PATH": "/runpod-volume/reports.sqlite",
        # Same reasoning as REPORT_DB_PATH: without this, the HF manifest is
        # written to container-local disk and is lost every time the worker
        # scales to zero, so /models_hf returns an empty list until someone
        # re-runs /models_hf/update.
        "MODELS_HF_PATH": "/runpod-volume/models_hf.json",
        # RunPod Secret, injected at runtime -- see dockerFiles/runner_handler.py's
        # identical HF_TOKEN handling for why this isn't a literal value.
        "HF_TOKEN": "{{ RUNPOD_SECRET_HF_TOKEN }}",
        "HF_ORG": "mflux-community",
        # The Docker GPU Runner's serverless endpoint id -- dispatch_trigger
        # (app/generate.py) needs this to know where to POST real jobs when
        # dispatch=true. Not a Flash resource, so there's no name-based
        # lookup available the way there is for mflux-orchestrator itself;
        # update this if the Runner endpoint is ever recreated (its id
        # changes each time, unlike this Flash-managed resource).
        "RUNNER_ENDPOINT_ID": "jx45e9ewmop06z",
        # DO Spaces outbox (app/outbox.py) -- KEY/SECRET are real credentials,
        # so (like HF_TOKEN above) they're RunPod Secret references, not
        # literal values, to avoid committing them to git. Create these via
        # the RunPod console (Settings -> Secrets) before deploying:
        # RUNPOD_SECRET_DO_SPACES_KEY, RUNPOD_SECRET_DO_SPACES_SECRET.
        "DO_SPACES_KEY": "{{ RUNPOD_SECRET_DO_SPACES_KEY }}",
        "DO_SPACES_SECRET": "{{ RUNPOD_SECRET_DO_SPACES_SECRET }}",
        "DO_SPACES_REGION": "nyc3",
        "DO_SPACES_ENDPOINT": "https://nyc3.digitaloceanspaces.com",
        "DO_SPACES_BUCKET": "mflux-runpod",
    },
)


@orchestrator.get("/models_supported")
async def models_supported() -> dict:
    from app.models_supported import load_models_supported

    return load_models_supported()


@orchestrator.get("/models_hf")
async def models_hf() -> dict:
    from app.models_hf import load_models_hf

    return load_models_hf()


@orchestrator.post("/models_hf/update")
async def models_hf_update() -> dict:
    from app.models_hf import update_models_hf

    return update_models_hf()


@orchestrator.get("/models_missing")
async def models_missing() -> dict:
    from app.models_missing import compute_missing, load_configs, load_overrides
    from app.models_hf import load_models_hf

    configs = load_configs()
    hf_manifest = load_models_hf()
    overrides = load_overrides()
    return compute_missing(configs, hf_manifest, overrides)


@orchestrator.get("/model_store")
async def model_store() -> dict:
    """List currently-active ephemeral per-series build volumes -- see
    app.runpod_volumes.list_active_series_volumes's docstring for why this
    is a build-scratch view, not the finished-model catalog (that's
    /models_hf)."""
    from app.runpod_volumes import list_active_series_volumes

    return {"volumes": list_active_series_volumes()}


@orchestrator.post("/generate")
async def generate(data: GenerateRequest) -> dict:
    """Plan+record one model's generation run. Dry-runs by default (plans
    and records a `runs` row only, no RunPod API calls) — pass
    dispatch=true to actually create/reuse the series' volume and dispatch
    real GPU jobs to the Runner (see app/generate.py::dispatch_trigger)."""
    from app.db import init_db
    from app.generate import (
        DispatchConfigError,
        UnknownModelError,
        dispatch_trigger,
        dry_run_trigger,
        generate_one,
    )

    # No FastAPI lifespan hook in Flash's load-balanced route mode (unlike
    # app/main.py), so each DB-touching route ensures the schema exists
    # itself. init_db() is CREATE TABLE IF NOT EXISTS -- cheap, idempotent,
    # safe to call on every request.
    init_db()
    trigger_fn = dispatch_trigger if data.dispatch else dry_run_trigger
    try:
        return generate_one(
            data.config_stem,
            quants=data.quants,
            mflux_repo=data.mflux_repo,
            mflux_branch=data.mflux_branch,
            force_hf_overwrite=data.force_hf_overwrite,
            trigger_fn=trigger_fn,
        )
    except UnknownModelError as exc:
        return {"error": str(exc)}
    except DispatchConfigError as exc:
        return {"error": str(exc)}


@orchestrator.post("/generate_all")
async def generate_all_route(data: GenerateAllRequest = GenerateAllRequest()) -> dict:
    """Plan+record a run for every series GET /models_missing reports. Same
    dispatch opt-in as /generate — defaults to dry-run."""
    from app.db import init_db
    from app.generate import DispatchConfigError, dispatch_trigger, dry_run_trigger, generate_all

    init_db()
    trigger_fn = dispatch_trigger if data.dispatch else dry_run_trigger
    try:
        return {"runs": generate_all(trigger_fn=trigger_fn)}
    except DispatchConfigError as exc:
        return {"error": str(exc)}


@orchestrator.post("/report/run/{run_id}")
async def report_run_callback(run_id: int, data: dict) -> dict:
    from app.db import init_db
    from app.report import add_quant_build, run_detail, update_run_status_from_children

    init_db()
    if run_detail(run_id) is None:
        return {"error": f"run {run_id} not found"}

    for qb in data.get("quant_builds", []):
        add_quant_build(
            run_id,
            qb["quant"],
            status=qb["status"],
            total_size_bytes=qb.get("total_size_bytes"),
            text_encoder_bytes=qb.get("text_encoder_bytes"),
            transformer_bytes=qb.get("transformer_bytes"),
            vae_bytes=qb.get("vae_bytes"),
            build_duration_s=qb.get("build_duration_s"),
            upload_duration_s=qb.get("upload_duration_s"),
            hf_repo_id=qb.get("hf_repo_id"),
        )

    update_run_status_from_children(
        run_id, finished_at=data["finished_at"], error=data.get("error")
    )
    return run_detail(run_id)


@orchestrator.get("/report")
async def report(model_series: str | None = None, run_id: int | None = None, limit: int = 20) -> dict:
    from app.db import init_db
    from app.report import recent_runs, run_detail, runs_for_series, summary

    init_db()
    if run_id is not None:
        detail = run_detail(run_id)
        return detail if detail is not None else {"error": f"run {run_id} not found"}
    if model_series is not None:
        return {"runs": runs_for_series(model_series, limit)}
    return {"runs": recent_runs(limit), "summary": summary()}


@orchestrator.get("/report/dump")
async def report_dump() -> dict:
    """Full raw JSON dump of every table -- runs (each with quant_builds),
    series_volumes, plus summary aggregates. Unlimited by design, unlike
    /report which paginates via `limit`."""
    from app.db import init_db
    from app.report import dump_all

    init_db()
    return dump_all()


@orchestrator.delete("/report")
async def report_clear() -> dict:
    """Deletes every runs + quant_builds row -- schema untouched, and does
    NOT delete series_volumes (those track real RunPod resources, not log
    entries). Irreversible; no confirmation step -- intentionally blunt
    maintenance tooling."""
    from app.db import init_db
    from app.report import clear_runs

    init_db()
    return clear_runs()


@orchestrator.post("/outbox/poll")
async def outbox_poll() -> dict:
    """Process every pending result currently sitting in the DO Spaces
    outbox right now (see app/outbox.py). Unlike app/main.py's version,
    there's no automatic background loop here -- Flash's LB lifespan is
    auto-generated (just logs start/stop), not something this module
    controls, and request-driven ephemeral workers aren't a good fit for
    a persistent poll loop anyway. This route needs an external trigger
    (cron, manual call, etc.) to actually run periodically."""
    from app.db import init_db
    from app.outbox import OutboxConfigError, process_pending

    init_db()
    try:
        return process_pending()
    except OutboxConfigError as exc:
        return {"error": str(exc)}


@orchestrator.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# No /ping route here -- Flash reserves that path for its own framework
# health check (confirmed: `path '/ping' is reserved by the framework`).
# app/main.py's /ping was redundant with /health anyway.
