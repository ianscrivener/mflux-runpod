"""Orchestrator API (PRD: (1) Orchestrator - CPU)."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.db import init_db
from app.generate import UnknownModelError, dry_run_trigger, generate_all, generate_one
from app.models_hf import load_models_hf, update_models_hf
from app.models_missing import compute_missing, load_configs, load_overrides
from app.models_supported import load_models_supported
from app.report import (
    add_quant_build,
    clear_runs,
    dump_all,
    recent_runs,
    run_detail,
    runs_for_series,
    summary,
    update_run_status_from_children,
)

logger = logging.getLogger(__name__)

OUTBOX_POLL_INTERVAL_S = 30


async def _outbox_poll_loop() -> None:
    """Background task: process the DO Spaces outbox every
    OUTBOX_POLL_INTERVAL_S seconds so a job's result gets applied whenever
    this process happens to be running, without needing a direct callback
    at the exact moment the job finishes (see app/outbox.py)."""
    from app.outbox import OutboxConfigError, process_pending

    while True:
        try:
            result = process_pending()
            if result["processed"] or result["errors"]:
                logger.info("outbox poll: %s", result)
        except OutboxConfigError:
            # Not configured (e.g. local dev without DO_SPACES_* set) --
            # don't retry every 30s forever, just stop polling.
            logger.warning("DO Spaces outbox not configured; background polling disabled")
            return
        except Exception:  # noqa: BLE001 - one bad poll shouldn't kill the loop
            logger.exception("outbox poll failed")
        await asyncio.sleep(OUTBOX_POLL_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    poll_task = asyncio.create_task(_outbox_poll_loop())
    yield
    poll_task.cancel()


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


@app.post("/models_missing/update", summary="Materialize + publish the current missing-models diff")
def models_missing_update():
    """GET /models_missing stays live-computed -- this snapshots that same
    result to data-hf-sync/models_missing.json and publishes it to the HF bucket, for
    anything consuming the bucket directly instead of calling the API."""
    from app.hf_datasets import HfDatasetConfigError
    from app.models_missing import refresh_models_missing

    try:
        return refresh_models_missing()
    except HfDatasetConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/runpod_gpu_skus/update", summary="Refresh RunPod GPU pricing and publish it")
def runpod_gpu_skus_update():
    from app.hf_datasets import HfDatasetConfigError
    from app.runpod_skus import RunpodSkusConfigError, refresh_gpu_skus

    try:
        return {"gpus": refresh_gpu_skus()}
    except (RunpodSkusConfigError, HfDatasetConfigError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/models_queue/publish", summary="Save local models_queue.json as the DO Spaces master + refresh the HF mirror")
def models_queue_publish():
    from app.hf_datasets import HfDatasetConfigError
    from app.queue_store import QueueStoreConfigError, publish

    try:
        return publish()
    except (QueueStoreConfigError, HfDatasetConfigError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/models_queue/restore", summary="Overwrite local models_queue.json from the DO Spaces master")
def models_queue_restore():
    from app.queue_store import QueueStoreConfigError, restore

    try:
        return restore()
    except QueueStoreConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class QueueEntryRequest(BaseModel):
    model_stem: str = Field(..., description="Must match a configs/models/{stem}.yaml", examples=["Fibo"])
    quants: list[str] | None = Field(default=None, description="Omit to mean 'whatever's missing at process time'")
    force_hf_overwrite: bool = Field(default=False)
    note: str | None = Field(default=None)


class QueueEntryUpdateRequest(BaseModel):
    status: str | None = Field(default=None, description="pending | approved | skipped")
    quants: list[str] | None = None
    force_hf_overwrite: bool | None = None
    note: str | None = None


@app.get("/models_queue", summary="List queue entries")
def models_queue_list():
    from app.queue import list_entries

    return {"entries": list_entries()}


@app.post("/models_queue", summary="Add a model series to the queue")
def models_queue_add(request: QueueEntryRequest):
    from app.queue import QueueValidationError, add_entry

    try:
        return add_entry(
            request.model_stem, request.quants, request.force_hf_overwrite, request.note
        )
    except QueueValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/models_queue/{entry_id}", summary="Update a queue entry")
def models_queue_update(entry_id: int, request: QueueEntryUpdateRequest):
    from app.queue import QueueValidationError, update_entry

    try:
        return update_entry(
            entry_id, request.status, request.quants, request.force_hf_overwrite, request.note
        )
    except QueueValidationError as exc:
        status = 404 if "no queue entry" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.delete("/models_queue/{entry_id}", summary="Remove a queue entry")
def models_queue_delete(entry_id: int):
    from app.queue import QueueValidationError, delete_entry

    try:
        return {"deleted": entry_id, **delete_entry(entry_id)}
    except QueueValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/datasets", summary="List the seven HF-bucket-backed datasets and their sync state")
def datasets_list():
    from app.hf_datasets import list_datasets

    return {"datasets": list_datasets()}


@app.post("/datasets/{name}/pull", summary="Pull one dataset from the HF bucket if it changed")
def datasets_pull(name: str):
    from app.hf_datasets import HfDatasetConfigError, pull

    try:
        return pull(name)
    except HfDatasetConfigError as exc:
        raise HTTPException(status_code=404 if "no such dataset" in str(exc) else 503, detail=str(exc)) from exc


@app.post("/datasets/{name}/push", summary="Push one dataset's local file to the HF bucket")
def datasets_push(name: str):
    from app.hf_datasets import HfDatasetConfigError, push

    try:
        return push(name)
    except HfDatasetConfigError as exc:
        status = 404 if "no such dataset" in str(exc) else 400 if "not writable" in str(exc) else 503
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/model_store")
def model_store():
    """List currently-active ephemeral per-series build volumes (NOT the
    model store itself -- finished quants live on Hugging Face, see
    /models_hf. These are RunPod build-scratch volumes; see
    app.runpod_volumes.list_active_series_volumes's docstring for why a
    listed volume is usually empty or holding one in-progress build)."""
    from app.runpod_volumes import list_active_series_volumes

    return {"volumes": list_active_series_volumes()}


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


@app.post("/generate", summary="Plan (and optionally dispatch) one model series' build")
def generate(request: GenerateRequest):
    """Plan+record one model's generation run. Dry-runs by default (plans
    and records a `runs` row only, no RunPod API calls) — pass
    dispatch=true to actually create/reuse the series' volume and dispatch
    real GPU jobs to the Runner (see app/generate.py::dispatch_trigger)."""
    from app.generate import DispatchConfigError, dispatch_trigger

    trigger_fn = dispatch_trigger if request.dispatch else dry_run_trigger
    try:
        return generate_one(
            request.config_stem,
            quants=request.quants,
            mflux_repo=request.mflux_repo,
            mflux_branch=request.mflux_branch,
            force_hf_overwrite=request.force_hf_overwrite,
            trigger_fn=trigger_fn,
        )
    except UnknownModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/generate/{run_id}/cancel", summary="Cancel a run's still-in-flight RunPod jobs")
def generate_cancel(run_id: int):
    """Best-effort: cancels every job dispatch_trigger recorded for this
    run_id that hasn't already been cancelled, then marks the run
    'cancelled' regardless of whether every individual cancel call
    succeeded (a job that already finished will fail to cancel, that's
    fine)."""
    from app.generate import DispatchConfigError, UnknownModelError, cancel_run

    try:
        return cancel_run(run_id)
    except UnknownModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class GenerateAllRequest(BaseModel):
    dispatch: bool = Field(
        default=False,
        description="Same opt-in as /generate's dispatch field, applied to every "
        "series GET /models_missing currently reports as missing at least one quant. "
        "false (default) dry-runs all of them; true dispatches a real GPU job per "
        "missing quant, across every missing series, in one call.",
    )

    model_config = {"json_schema_extra": {"examples": [{"dispatch": False}]}}


@app.post("/generate_all", summary="Plan (and optionally dispatch) every missing series")
def generate_all_endpoint(request: GenerateAllRequest = GenerateAllRequest()):
    """Plan+record a run for every series /models_missing reports. Same
    dispatch opt-in as /generate — defaults to dry-run."""
    from app.generate import DispatchConfigError, dispatch_trigger

    trigger_fn = dispatch_trigger if request.dispatch else dry_run_trigger
    try:
        return {"runs": generate_all(trigger_fn=trigger_fn)}
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


class RunStatusCallbackEnvelope(BaseModel):
    """Wire format is {"data": {...}} at the top level, not a bare
    RunStatusCallback -- this matches app/orchestrator_endpoint.py's Flash
    load-balanced route (which requires args wrapped under a key matching
    its param name, confirmed live), so dockerFiles/runner_handler.py and
    app/runner_endpoint.py can POST the identical payload shape to either
    Orchestrator entrypoint without knowing which one they're talking to."""

    data: RunStatusCallback


@app.post("/report/run/{run_id}")
def report_run_callback(run_id: int, envelope: RunStatusCallbackEnvelope):
    callback = envelope.data
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


@app.delete("/report", summary="Clear the generation log (runs + quant_builds)")
def report_clear():
    """Deletes every runs + quant_builds row -- schema untouched, and does
    NOT delete series_volumes (those track real RunPod resources, not log
    entries). Irreversible; there's no confirmation step, so treat this as
    intentionally blunt maintenance tooling, not something to wire up to a
    casual UI button."""
    return clear_runs()


@app.post("/outbox/poll", summary="Process the DO Spaces outbox right now")
def outbox_poll():
    """Process every pending result currently sitting in the DO Spaces
    outbox immediately, instead of waiting for the background loop's next
    tick (every OUTBOX_POLL_INTERVAL_S seconds -- see app/outbox.py).
    Useful for testing, or forcing an immediate catch-up after being
    offline for a while."""
    from app.outbox import OutboxConfigError, process_pending

    try:
        return process_pending()
    except OutboxConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/health")
def health():
    return {"status": "ok"}
