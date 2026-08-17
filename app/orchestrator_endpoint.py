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

Uses `mflux-global-s3` -- the persistent volume created by hand in the RunPod
console (has RunPod's "Global Networking" + S3-compatible API support) --
rather than letting Flash auto-create a volume, so this Orchestrator's
storage is the one, deliberately-provisioned volume, not a Flash-managed one
that could get recreated under a different id on some future deploy.

Network volumes are datacenter-pinned on RunPod, not truly cross-region (
confirmed against runpod_flash's own NetworkVolume/serverless resource code
and RunPod's docs, 2026-08-17) -- "Global Networking" is a per-datacenter
capability flag enabling the S3-compatible HTTP API, not a volume reachable
natively from every region. US-IL-1 was chosen because it has that flag;
every Endpoint in this project (this one, mflux-runner, mflux-runner-health)
is pinned to the same datacenter so all three can actually mount it.

Per the Flash skill's Gotcha #1 (only the function body ships to `flash dev`
workers), every import each route needs is defined inside that route's own
body, matching the pattern in app/runner_endpoint.py.

NOT deployed by default -- this module only defines the endpoint. Deploying
it (`flash deploy`) or running it against `flash dev` provisions/uses real,
billed RunPod CPU workers + a network volume. Do that deliberately.
"""

from runpod_flash import CpuInstanceType, DataCenter, Endpoint, NetworkVolume

ORCHESTRATOR_DATACENTER = DataCenter.US_IL_1

orchestrator = Endpoint(
    name="mflux-orchestrator",
    cpu=CpuInstanceType.CPU3C_1_2,
    workers=(0, 1),
    idle_timeout=60,
    datacenter=ORCHESTRATOR_DATACENTER,
    dependencies=["pyyaml", "httpx"],
    volume=NetworkVolume(name="mflux-global-s3", datacenter=ORCHESTRATOR_DATACENTER, size=10),
    env={
        "REPORT_DB_PATH": "/runpod-volume/reports.sqlite",
        # RunPod Secret, injected at runtime -- see app/runner_endpoint.py's
        # identical HF_TOKEN handling for why this isn't a literal value.
        "HF_TOKEN": "{{ RUNPOD_SECRET_HF_TOKEN }}",
        "HF_ORG": "mflux-community",
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


@orchestrator.post("/generate")
async def generate(data: dict) -> dict:
    from app.db import init_db
    from app.generate import UnknownModelError, generate_one

    # No FastAPI lifespan hook in Flash's load-balanced route mode (unlike
    # app/main.py), so each DB-touching route ensures the schema exists
    # itself. init_db() is CREATE TABLE IF NOT EXISTS -- cheap, idempotent,
    # safe to call on every request.
    init_db()
    try:
        return generate_one(
            data["config_stem"],
            quants=data.get("quants"),
            mflux_repo=data.get("mflux_repo"),
            mflux_branch=data.get("mflux_branch"),
            force_hf_overwrite=data.get("force_hf_overwrite", False),
        )
    except UnknownModelError as exc:
        return {"error": str(exc)}


@orchestrator.post("/generate_all")
async def generate_all_route() -> dict:
    from app.db import init_db
    from app.generate import generate_all

    init_db()
    return {"runs": generate_all()}


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


@orchestrator.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# No /ping route here -- Flash reserves that path for its own framework
# health check (confirmed: `path '/ping' is reserved by the framework`).
# app/main.py's /ping was redundant with /health anyway.
