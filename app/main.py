"""Orchestrator API (PRD: (1) Orchestrator - CPU)."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Confirmed live 2026-08-20: a project .env (HF_WORKER_URL, historically
# DO_SPACES_*) has existed for a while but was never actually loaded into
# os.environ by anything -- every var in it only ever reached the app if the
# launching shell happened to export it itself. Load it explicitly, before
# any of the app.* imports below run (several read os.environ at import
# time or in module-level constants). Doesn't override already-exported
# real env vars (override=False, the default), so a real deployment's own
# secrets still win over a stray local .env.
load_dotenv()

from app.db import init_db
from app.generate import UnknownModelError, dry_run_trigger, generate_one
from app.models_hf import update_models_hf
from app.models_missing import compute_missing, load_configs, load_overrides
from app.report import (
    add_quant_build,
    clear_runs,
    delete_run,
    dump_all,
    recent_runs,
    run_detail,
    runs_for_series,
    summary,
    update_run_status_from_children,
)

logger = logging.getLogger(__name__)

# The webapp polls these endpoints every few seconds; suppress their uvicorn
# access-log lines so `just serve` output stays readable.
_QUIET_ACCESS_LOG_PATHS = ("/models_missing", "/health", "/report", "/models_queue")


class _QuietPollingEndpoints(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(f"GET {path}" in message for path in _QUIET_ACCESS_LOG_PATHS)


logging.getLogger("uvicorn.access").addFilter(_QuietPollingEndpoints())

POLLING_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "config.yaml"
HF_HARDWARE_PATH = Path(__file__).resolve().parent.parent / "data-hf-sync" / "hf_hardware.json"


def load_polling_config() -> dict:
    """Background-loop polling intervals, externalized to configs/config.yaml
    so they're tunable without a code change. Path resolved relative to this
    file, not cwd -- same convention as app/hf_datasets.py's CONFIG_PATH."""
    import yaml

    with open(POLLING_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["polling"]


_polling = load_polling_config()
OUTBOX_POLL_INTERVAL_S = _polling["outbox_poll_interval_s"]
MODELS_HF_REFRESH_INTERVAL_S = _polling["models_hf_refresh_interval_s"]
HF_SYNC_INTERVAL_S = _polling["hf_sync_interval_s"]
MODELS_SRC_DETAILS_REFRESH_INTERVAL_S = _polling["models_src_details_refresh_interval_s"]


async def _outbox_poll_loop() -> None:
    """Background task: process the HF bucket outbox every
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
            # Not configured (e.g. local dev without HF_TOKEN set) --
            # don't retry every 30s forever, just stop polling.
            logger.warning("HF bucket outbox not configured; background polling disabled")
            return
        except Exception:  # noqa: BLE001 - one bad poll shouldn't kill the loop
            logger.exception("outbox poll failed")
        await asyncio.sleep(OUTBOX_POLL_INTERVAL_S)


def _stale(path: Path, max_age_s: float) -> bool:
    """True if `path` is missing or wasn't written within the last max_age_s
    seconds -- used to skip a refresh that's already fresh (e.g. someone hit
    the manual /models_hf/update button 30s ago)."""
    if not path.exists():
        return True
    return (time.time() - path.stat().st_mtime) >= max_age_s


async def _models_hf_refresh_loop() -> None:
    """Background task: rescan the mflux-community HF org and refresh
    data-hf-sync/models_hf.json at startup and every
    MODELS_HF_REFRESH_INTERVAL_S seconds after, skipping the scan if the
    manifest is already fresher than that -- keeps /models_hf and
    /models_missing current without hammering the HF API on every restart."""
    from app.hf_datasets import HfDatasetConfigError
    from app.models_catalog import rebuild_if_needed
    from app.models_hf import DATA_PATH

    while True:
        try:
            if _stale(DATA_PATH, MODELS_HF_REFRESH_INTERVAL_S):
                result = await asyncio.to_thread(update_models_hf)
                logger.info("models_hf refresh: %d models", len(result.get("hf_models", [])))
            rebuild_if_needed()
        except HfDatasetConfigError:
            logger.warning("HF_TOKEN not configured; models_hf background refresh disabled")
            return
        except Exception:  # noqa: BLE001 - one bad scan shouldn't kill the loop
            logger.exception("models_hf refresh failed")
        await asyncio.sleep(MODELS_HF_REFRESH_INTERVAL_S)


async def _models_src_details_refresh_loop() -> None:
    """Background task: rescan every model's upstream source repo (size,
    commit hash, last-modified date, text encoder) and refresh
    data-hf-sync/models_src_details.json at startup and every
    MODELS_SRC_DETAILS_REFRESH_INTERVAL_S seconds after, skipping the scan if
    the manifest is already fresher than that. A much longer interval than
    the other loops -- unlike models_hf (one list call), this makes one HF
    API call (+ an optional model_index.json download) per distinct source
    repo, and source repos change on the order of days/weeks, not minutes."""
    from app.hf_datasets import HfDatasetConfigError
    from app.models_catalog import rebuild_if_needed
    from app.models_src_details import DATA_PATH, refresh_models_src_details

    while True:
        try:
            if _stale(DATA_PATH, MODELS_SRC_DETAILS_REFRESH_INTERVAL_S):
                result = await asyncio.to_thread(refresh_models_src_details)
                logger.info("models_src_details refresh: %d source repos", len(result))
            rebuild_if_needed()
        except HfDatasetConfigError:
            logger.warning("HF_TOKEN not configured; models_src_details background refresh disabled")
            return
        except Exception:  # noqa: BLE001 - one bad scan shouldn't kill the loop
            logger.exception("models_src_details refresh failed")
        await asyncio.sleep(MODELS_SRC_DETAILS_REFRESH_INTERVAL_S)


async def _hf_sync_loop() -> None:
    """Background task: pull every dataset configured in
    configs/hf_datasets.yaml (models_mflux, models_missing, models_queue,
    etc.) from the HF bucket at startup and every HF_SYNC_INTERVAL_S seconds
    after, so this process's data-hf-sync/ mirror stays current with
    anything another writer (or the upstream mflux CI pipeline, for
    models_mflux) published. pull() is metadata-only when nothing changed,
    so this is cheap even at a 5-minute cadence."""
    import os

    from app.hf_datasets import HfDatasetConfigError, load_dataset_config, pull
    from app.models_catalog import rebuild_if_needed

    if not os.environ.get("HF_TOKEN"):
        logger.warning("HF_TOKEN not configured; HF dataset sync disabled")
        return

    dataset_names = list(load_dataset_config().get("datasets", {}))

    while True:
        for name in dataset_names:
            try:
                await asyncio.to_thread(pull, name)
            except HfDatasetConfigError as exc:
                # Per-dataset, recoverable (e.g. a dataset that's never been
                # pushed yet, so it doesn't exist in the bucket) -- log and
                # move on to the next dataset rather than disabling the
                # whole loop, which HF_TOKEN's absence (checked once above)
                # would warrant instead.
                logger.warning("hf sync: pull(%r) skipped: %s", name, exc)
            except Exception:  # noqa: BLE001 - one bad pull shouldn't kill the loop
                logger.exception("hf sync: pull(%r) failed", name)
        try:
            rebuild_if_needed()
        except Exception:  # noqa: BLE001 - a failed rebuild here just means the next GET retries it
            logger.exception("catalog rebuild after hf sync failed")
        await asyncio.sleep(HF_SYNC_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.models_catalog import rebuild_if_needed

    try:
        rebuild_if_needed()
    except Exception:  # noqa: BLE001 - a failed rebuild here just means the first GET retries it
        logger.exception("catalog rebuild at startup failed")
    poll_task = asyncio.create_task(_outbox_poll_loop())
    models_hf_task = asyncio.create_task(_models_hf_refresh_loop())
    models_src_details_task = asyncio.create_task(_models_src_details_refresh_loop())
    hf_sync_task = asyncio.create_task(_hf_sync_loop())
    yield
    poll_task.cancel()
    models_hf_task.cancel()
    models_src_details_task.cancel()
    hf_sync_task.cancel()


app = FastAPI(title="mflux-conv orchestrator", lifespan=lifespan)


@app.get("/models_mflux")
def models_mflux():
    from app.models_catalog import get_mflux_catalog

    return get_mflux_catalog()


@app.get("/models_hf")
def models_hf():
    from app.models_catalog import get_published_hf_manifest

    return get_published_hf_manifest()


@app.post("/models_hf/update")
def models_hf_update():
    return update_models_hf()


@app.get("/models_hf/card_preview")
def models_hf_card_preview():
    from app.model_card import render_sample_model_card

    try:
        return {"markdown": render_sample_model_card()}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/models_missing")
def models_missing():
    from app.models_catalog import get_published_hf_manifest
    from app.models_missing import load_models_skipped

    configs = load_configs()
    hf_manifest = get_published_hf_manifest()
    overrides = load_overrides()
    skip_rules = load_models_skipped()
    return compute_missing(configs, hf_manifest, overrides, skip_rules)


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


@app.get("/models_src_details", summary="Per-model upstream source repo size/hash/date/text-encoder")
def models_src_details():
    from app.models_catalog import get_models_src_details

    return get_models_src_details()


@app.get(
    "/models_identity",
    summary="Authoritative stem -> catalog slug/family/type/quants resolution",
)
def models_identity():
    from app.models_catalog import get_model_identities

    return get_model_identities()


@app.get(
    "/text_encoder_aliases",
    summary="Human-friendly aliases for raw text-encoder class names (data/text-encoder-alias.csv)",
)
def text_encoder_aliases():
    from app.text_encoder_aliases import load_text_encoder_aliases

    return load_text_encoder_aliases()


@app.get(
    "/models_available",
    summary="models_mflux.json x default quants - models_skipped.json (informational, not dispatch)",
)
def models_available():
    from app.models_catalog import get_available_models

    return get_available_models()


@app.post(
    "/models_skipped/refresh",
    summary="Force-rebuild the catalog mirror and re-read data-hf-sync/models_skipped.json",
)
def models_skipped_refresh():
    from app.models_catalog import get_available_models, rebuild_if_needed

    rebuild_if_needed(force=True)
    return get_available_models()


@app.post(
    "/models_src_details/update",
    summary="Rescan every model's upstream source repo and publish the details",
)
def models_src_details_update():
    from app.hf_datasets import HfDatasetConfigError
    from app.models_src_details import refresh_models_src_details

    try:
        return refresh_models_src_details()
    except HfDatasetConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/models_queue/publish", summary="Save local models_queue.json as the HF-bucket master + refresh the HF mirror")
def models_queue_publish():
    from app.hf_datasets import HfDatasetConfigError
    from app.queue_store import QueueStoreConfigError, publish

    try:
        return publish()
    except (QueueStoreConfigError, HfDatasetConfigError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/models_queue/restore", summary="Overwrite local models_queue.json from the HF-bucket master")
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
    from app.queue import QueueStorageError, list_entries

    try:
        return {"entries": list_entries()}
    except QueueStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/models_queue", summary="Add a model series to the queue")
def models_queue_add(request: QueueEntryRequest):
    from app.queue import QueueStorageError, QueueValidationError, add_entry

    try:
        return add_entry(
            request.model_stem, request.quants, request.force_hf_overwrite, request.note
        )
    except QueueValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QueueStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.patch("/models_queue/{entry_id}", summary="Update a queue entry")
def models_queue_update(entry_id: int, request: QueueEntryUpdateRequest):
    from app.queue import QueueStorageError, QueueValidationError, update_entry

    # exclude_unset so an omitted JSON field never reaches update_entry --
    # only fields the caller actually sent get applied, letting quants/note
    # be explicitly cleared back to null without touching the rest.
    try:
        return update_entry(entry_id, **request.model_dump(exclude_unset=True))
    except QueueValidationError as exc:
        status = 404 if "no queue entry" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except QueueStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/models_queue/{entry_id}", summary="Remove a queue entry")
def models_queue_delete(entry_id: int):
    from app.queue import QueueStorageError, QueueValidationError, delete_entry

    try:
        return {"deleted": entry_id, **delete_entry(entry_id)}
    except QueueValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QueueStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/datasets", summary="List the eight HF-bucket-backed datasets and their sync state")
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


class GenerateRequest(BaseModel):
    config_stem: str = Field(
        ...,
        description="Model series to generate, matching a configs/models/{config_stem}.yaml "
        "file exactly (see GET /models_mflux or /models_missing for valid values). "
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
        description="Recorded on the `runs` row for audit purposes only -- the HF "
        "Spaces GPU worker installs a fixed mflux from PyPI at image-build time and "
        "has no per-run override mechanism (unlike the old RunPod Runner). Omit to "
        "use the default.",
        examples=["mflux (PyPI)"],
    )
    mflux_branch: str | None = Field(
        default=None,
        description="Same audit-only caveat as mflux_repo -- no per-run effect on "
        "what the worker actually builds. Omit for \"main\".",
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
        "a `runs` row only, dispatches nothing. true = dispatches one real, billed "
        "GPU job per quant to the HF Spaces worker (see app/generate.py::dispatch_trigger) "
        "-- requires HF_WORKER_URL configured in the Orchestrator's environment, "
        "503s with a clear message otherwise.",
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
    and records a `runs` row only) — pass dispatch=true to dispatch real GPU
    jobs to the HF Spaces worker (see app/generate.py::dispatch_trigger)."""
    from app.generate import DispatchConfigError, InvalidQuantsError, dispatch_trigger

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
    except InvalidQuantsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/generate/{run_id}/cancel", summary="Cancel a run's still-in-flight GPU jobs")
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


@app.get("/gpu/status", summary="HF Spaces GPU worker's current build state, queue depth, prefetches")
def gpu_status():
    from app.generate import DispatchConfigError, worker_status
    import httpx

    try:
        return worker_status()
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"worker unreachable: {exc}") from exc


@app.post("/gpu/pause", summary="Pause the GPU worker's HF Space")
def gpu_pause():
    from app.generate import DispatchConfigError, pause_worker
    import httpx

    try:
        return pause_worker()
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"pause failed: {exc}") from exc


@app.post("/gpu/start", summary="Restart/resume the GPU worker's HF Space")
def gpu_start():
    from app.generate import DispatchConfigError, start_worker
    import httpx

    try:
        return start_worker()
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"start failed: {exc}") from exc


class GpuHardwareRequest(BaseModel):
    hardware: str


@app.post("/gpu/hardware", summary="Change the GPU worker's HF Space hardware tier (restarts the Space)")
def gpu_set_hardware(req: GpuHardwareRequest):
    from app.generate import DispatchConfigError, InvalidHardwareError, set_worker_hardware
    import httpx

    try:
        return set_worker_hardware(req.hardware)
    except InvalidHardwareError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"setting hardware failed: {exc}") from exc


@app.get("/gpu/logs/build", summary="GPU worker Space's container build logs")
def gpu_logs_build():
    from app.generate import DispatchConfigError, fetch_worker_logs
    import httpx

    try:
        return fetch_worker_logs(build=True)
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetching build logs failed: {exc}") from exc


@app.get("/gpu/logs/container", summary="GPU worker Space's running-container stdout/stderr")
def gpu_logs_container():
    from app.generate import DispatchConfigError, fetch_worker_logs
    import httpx

    try:
        return fetch_worker_logs(build=False)
    except DispatchConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetching container logs failed: {exc}") from exc


@app.get("/gpu/hardware", summary="HF Spaces hardware tiers: specs + pricing (data-hf-sync/hf_hardware.json)")
def gpu_hardware():
    import json

    if not HF_HARDWARE_PATH.exists():
        return {"tiers": []}
    with open(HF_HARDWARE_PATH, encoding="utf-8") as f:
        return {"tiers": json.load(f)}


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
    RunStatusCallback -- kept from the RunPod-era dual-deployment shape
    (a second Flash-hosted Orchestrator entrypoint, since removed, required
    args wrapped under a key matching its param name) so any worker's
    callback payload doesn't need to change if that shape comes back."""

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
    NOT delete series_volumes (a legacy RunPod-era table, not log entries).
    Irreversible; there's no confirmation step, so treat this as
    intentionally blunt maintenance tooling, not something to wire up to a
    casual UI button."""
    return clear_runs()


@app.delete("/report/run/{run_id}", summary="Delete one run + its quant_builds rows")
def report_delete_run(run_id: int):
    """Removes a single run record -- e.g. a stale 'running' entry left
    behind by a cancelled/orphaned dispatch (see generate_cancel, which
    marks the *build job* cancelled but was never meant to also be the way
    to clean up its row from the log)."""
    if run_detail(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return delete_run(run_id)


@app.post("/outbox/poll", summary="Process the HF bucket outbox right now")
def outbox_poll():
    """Process every pending result currently sitting in the HF bucket
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


# Admin web app (webapp/, Svelte + Vite) -- built to app/static/ by
# `npm run build` (see webapp/vite.config.js). Mounted last, and only if
# the build actually exists, so every API route above still wins its exact
# path match and a not-yet-built checkout doesn't crash the app on import.
WEBAPP_DIST = Path(__file__).resolve().parent / "static"
if WEBAPP_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEBAPP_DIST, html=True), name="webapp")
