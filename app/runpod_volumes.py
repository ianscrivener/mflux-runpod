"""Ephemeral per-model-series RunPod Network Volumes (PRD: Storage section).

One volume per upstream model series (e.g. "Qwen-Image"), created the first time
a missing quant is found for that series, deleted once every quant for that
series is uploaded and verified on HF. Holds downloaded source weights and
in-progress/completed quantized build artifacts so a crashed Runner job resumes
without rebuilding finished quants.

Volume names are `mflux-{short-uuid}-{model_series}` (unique per creation, not
deterministic from model_series alone), because RunPod volume names aren't
guaranteed unique account-wide and a name collision would silently attach to
the wrong series' volume. The actual model_series -> volume_id lookup is a
DB query (app.db's series_volumes table, keyed by model_series with
deleted_at IS NULL marking the active one), not a name scan -- the uuid'd
name is decoration for the RunPod console, not the lookup key.
"""

import os
import uuid

import httpx

RUNPOD_API_BASE = "https://rest.runpod.io/v1"
# US-IL-1: has RunPod's "Global Networking" + S3-compatible API support (per
# live console check, 2026-08-17) -- matches the datacenter pinned for
# mflux-orchestrator/mflux-runner/mflux-runner-health, so every Endpoint in
# this project can actually mount a volume in this datacenter.
DEFAULT_DATA_CENTER_ID = os.environ.get("RUNPOD_DATA_CENTER_ID", "US-IL-1")
REQUEST_TIMEOUT = httpx.Timeout(30.0)

# Extra space beyond the upstream source model's own size, for the
# quantized build artifacts mflux produces alongside/after downloading
# source weights (build outputs coexist with source/ until
# series_lifecycle.delete_source_weights() frees it). Fixed for now rather
# than computed per-quant -- revisit if a series' build artifacts turn out
# to regularly exceed this.
VOLUME_HEADROOM_GB = 30

MIN_VOLUME_SIZE_GB = 10  # RunPod's own NetworkVolume minimum (pydantic ge=10)


def size_for_series(hf_model_name: str) -> int:
    """Ephemeral volume size for a model series: its upstream source
    model's actual size on HF, plus VOLUME_HEADROOM_GB for build artifacts.
    Replaces a flat guessed default -- e.g. Qwen-Image-Edit's source weights
    are ~63GB, dwarfing much smaller series like Fibo, so one fixed size for
    every series either wastes money or risks running out of room."""
    from app.models_hf import hf_repo_size_gb

    source_size_gb = hf_repo_size_gb(hf_model_name)
    return max(MIN_VOLUME_SIZE_GB, int(source_size_gb + VOLUME_HEADROOM_GB))


def _default_client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT)


def _headers() -> dict:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError("RUNPOD_API_KEY must be set in the environment")
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def volume_name_for_series(model_series: str) -> str:
    """Generates a fresh unique name each call -- NOT deterministic/reusable
    from model_series alone. Callers that need to find an existing series'
    volume must go through find_volume_for_series (a DB lookup), not
    reconstruct this name."""
    short_uuid = uuid.uuid4().hex[:8]
    return f"mflux-{short_uuid}-{model_series}"


def list_volumes(client: httpx.Client | None = None) -> list[dict]:
    client = client or _default_client()
    response = client.get(f"{RUNPOD_API_BASE}/networkvolumes", headers=_headers())
    response.raise_for_status()
    return response.json()


def find_volume_for_series(model_series: str) -> dict | None:
    """Looks up the active (deleted_at IS NULL) volume for a series from
    app.db's series_volumes table -- the RunPod volume name is no longer the
    lookup key (see module docstring), so this does NOT list/scan RunPod's
    API at all. Returns None if no active volume is on record."""
    from app.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT volume_id, volume_name FROM series_volumes "
            "WHERE model_series = ? AND deleted_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (model_series,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row["volume_id"], "name": row["volume_name"]}


def _record_series_volume(model_series: str, volume_id: str, volume_name: str) -> None:
    from datetime import datetime, timezone

    from app.db import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO series_volumes (model_series, volume_id, volume_name, created_at) "
            "VALUES (?, ?, ?, ?)",
            (model_series, volume_id, volume_name, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def create_volume(
    model_series: str,
    size_gb: int = 100,
    data_center_id: str = DEFAULT_DATA_CENTER_ID,
    client: httpx.Client | None = None,
) -> dict:
    """Create (or return the existing, per series_volumes) ephemeral volume
    for a model series. Newly-created volumes are recorded in series_volumes
    so a later call for the same model_series reuses this one instead of
    creating a duplicate."""
    existing = find_volume_for_series(model_series)
    if existing is not None:
        return existing

    client = client or _default_client()
    name = volume_name_for_series(model_series)
    payload = {
        "name": name,
        "dataCenterId": data_center_id,
        "size": size_gb,
    }
    response = client.post(
        f"{RUNPOD_API_BASE}/networkvolumes", headers=_headers(), json=payload
    )
    response.raise_for_status()
    volume = response.json()
    _record_series_volume(model_series, volume["id"], volume.get("name", name))
    return volume


def get_volume(volume_id: str, client: httpx.Client | None = None) -> dict:
    client = client or _default_client()
    response = client.get(
        f"{RUNPOD_API_BASE}/networkvolumes/{volume_id}", headers=_headers()
    )
    response.raise_for_status()
    return response.json()


def list_active_series_volumes(client: httpx.Client | None = None) -> list[dict]:
    """List every ephemeral per-series volume currently on record (deleted_at
    IS NULL in series_volumes), enriched with live size/datacenter from
    RunPod. These are build-scratch volumes, not the model store itself —
    build_and_upload_one_quant deletes each quant's local build directory
    right after a successful upload, so a listed volume is usually either
    empty or holding exactly one in-progress build, not a catalog of
    finished models (those live on Hugging Face; see app.models_hf).

    A volume whose RunPod resource no longer exists (deleted outside this
    app, or a stale DB row) is still listed with its DB fields but
    runpod=None, rather than raising — a listing endpoint shouldn't 500 over
    one stale entry."""
    from app.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT model_series, volume_id, volume_name, created_at "
            "FROM series_volumes WHERE deleted_at IS NULL ORDER BY created_at DESC"
        ).fetchall()

    client = client or _default_client()
    result = []
    for row in rows:
        entry = {
            "model_series": row["model_series"],
            "volume_id": row["volume_id"],
            "volume_name": row["volume_name"],
            "created_at": row["created_at"],
            "runpod": None,
        }
        try:
            volume = get_volume(row["volume_id"], client=client)
            entry["runpod"] = {
                "size_gb": volume.get("size"),
                "data_center_id": volume.get("dataCenterId"),
            }
        except httpx.HTTPStatusError:
            pass
        result.append(entry)
    return result


def delete_volume(volume_id: str, client: httpx.Client | None = None) -> None:
    """Delete a model series' volume once every quant is uploaded and
    verified. Also marks the matching series_volumes row as deleted (if one
    exists) so find_volume_for_series stops returning it -- callers that
    know the model_series should prefer series_lifecycle.teardown_if_complete
    over calling this directly, since that keeps the DB row in sync."""
    client = client or _default_client()
    response = client.delete(
        f"{RUNPOD_API_BASE}/networkvolumes/{volume_id}", headers=_headers()
    )
    response.raise_for_status()

    from datetime import datetime, timezone

    from app.db import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE series_volumes SET deleted_at = ? "
            "WHERE volume_id = ? AND deleted_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), volume_id),
        )
        conn.commit()
