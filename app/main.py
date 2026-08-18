"""Orchestrator API (PRD: (1) Orchestrator - CPU)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.db import init_db
from app.generate import UnknownModelError, generate_all, generate_one
from app.models_hf import load_models_hf, update_models_hf
from app.models_missing import compute_missing, load_configs, load_overrides
from app.models_supported import load_models_supported
from app.report import (
    add_quant_build,
    dump_all,
    recent_runs,
    run_detail,
    runs_for_series,
    summary,
    update_run_status_from_children,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="mflux-runpod orchestrator", lifespan=lifespan)


@app.get("/models_supported")
def models_supported():
    return load_models_supported()


@app.get("/models_hf")
def models_hf():
    return load_models_hf()


@app.post("/models_hf/update")
def models_hf_update():
    return update_models_hf()


@app.get("/models_missing")
def models_missing():
    configs = load_configs()
    hf_manifest = load_models_hf()
    overrides = load_overrides()
    return compute_missing(configs, hf_manifest, overrides)


class GenerateRequest(BaseModel):
    hf_model_name: str | None = None  # informational only; config_stem resolves the config
    config_stem: str
    mflux_repo: str | None = None
    mflux_branch: str | None = None
    quants: list[str] | None = None
    force_hf_overwrite: bool = False


@app.post("/generate")
def generate(request: GenerateRequest):
    """Plan+record one model's generation run. NOTE: no live GPU Runner is
    deployed yet (see ToDo.md task 14/15) — this always dry-runs (plans and
    records a `runs` row only) without provisioning storage or dispatching a
    real, billed GPU job."""
    try:
        return generate_one(
            request.config_stem,
            quants=request.quants,
            mflux_repo=request.mflux_repo,
            mflux_branch=request.mflux_branch,
            force_hf_overwrite=request.force_hf_overwrite,
        )
    except UnknownModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/generate_all")
def generate_all_endpoint():
    """Plan+record a run for every series /models_missing reports. Same
    dry-run safety note as /generate — no real GPU work is dispatched yet."""
    return {"runs": generate_all()}


class QuantBuildReport(BaseModel):
    quant: str
    status: str  # built | uploaded | skipped_existing | failed
    total_size_bytes: int | None = None
    text_encoder_bytes: int | None = None
    transformer_bytes: int | None = None
    vae_bytes: int | None = None
    build_duration_s: float | None = None
    upload_duration_s: float | None = None
    hf_repo_id: str | None = None


class RunStatusCallback(BaseModel):
    """Runner -> Orchestrator status callback (PRD task 7, second half).
    One GPU job = one quant, so N jobs post to this same run_id independently.
    The run's aggregate status is DERIVED from all reported quant_builds
    (update_run_status_from_children), not taken from any single job's
    opinion — a caller-supplied run-level status here would let whichever job
    posts last silently overwrite an earlier failure with its own success."""

    finished_at: str
    error: str | None = None
    quant_builds: list[QuantBuildReport] = []


@app.post("/report/run/{run_id}")
def report_run_callback(run_id: int, callback: RunStatusCallback):
    if run_detail(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    for qb in callback.quant_builds:
        add_quant_build(
            run_id,
            qb.quant,
            status=qb.status,
            total_size_bytes=qb.total_size_bytes,
            text_encoder_bytes=qb.text_encoder_bytes,
            transformer_bytes=qb.transformer_bytes,
            vae_bytes=qb.vae_bytes,
            build_duration_s=qb.build_duration_s,
            upload_duration_s=qb.upload_duration_s,
            hf_repo_id=qb.hf_repo_id,
        )

    update_run_status_from_children(run_id, finished_at=callback.finished_at, error=callback.error)

    return run_detail(run_id)


@app.get("/report")
def report(model_series: str | None = None, run_id: int | None = None, limit: int = 20):
    if run_id is not None:
        detail = run_detail(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return detail
    if model_series is not None:
        return {"runs": runs_for_series(model_series, limit)}
    return {"runs": recent_runs(limit), "summary": summary()}


@app.get("/report/dump")
def report_dump():
    """Full raw JSON dump of every table (runs + quant_builds +
    series_volumes + summary). Unlimited by design, unlike /report."""
    return dump_all()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping():
    return {"ping": "pong"}
