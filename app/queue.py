"""CRUD for the build queue (models_queue.json).

Deliberately separate from app.queue_store, which only handles moving the
whole file to/from its DO Spaces master -- this module is where entries
actually get added/edited/removed. Every mutation writes the local file then
calls queue_store.publish() immediately (low write volume, human-curated,
so "always current on the real master" is worth more here than batching
writes would save).

Schema: {"entries": [{"id", "model_stem", "quants", "force_hf_overwrite",
"status", "note", "added_at"}]}. quants: null means "whatever
/models_missing currently reports as missing for this series" at process
time, not a frozen list -- same semantics as /generate's own quants=null.
status is human-set state, not derived from anything (build/run wiring for
"processing the queue" is a separate, not-yet-built feature -- this only
covers CRUD on the list itself, see docs/security-audit-tasks.md-adjacent
2026-08-19 scoping discussion).
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.queue_store import LOCAL_PATH

VALID_STATUSES = ("pending", "approved", "skipped")


class QueueValidationError(ValueError):
    """Bad request -- unknown model_stem, unknown status, missing entry."""


def _load() -> dict:
    if not LOCAL_PATH.exists():
        return {"entries": []}
    try:
        data = json.loads(LOCAL_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"entries": []}
    if "entries" not in data or not isinstance(data["entries"], list):
        # Covers the {"place": "holder"} stub this file started life as.
        return {"entries": []}
    return data


def _save(data: dict) -> None:
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=LOCAL_PATH.parent, delete=False
    ) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(LOCAL_PATH)


def _publish() -> dict:
    """Best-effort: the local write above already succeeded and is the
    thing the caller actually asked for, so a durable-publish failure
    (DO Spaces/HF not configured, e.g. local dev) must not fail the whole
    CRUD call -- confirmed live, 2026-08-19: an unconfigured DO_SPACES_*
    turned every add/update/delete into a raw 500 despite the local file
    being written correctly."""
    from app.hf_datasets import HfDatasetConfigError
    from app.queue_store import QueueStoreConfigError, publish

    try:
        publish()
        return {"published": True}
    except (QueueStoreConfigError, HfDatasetConfigError) as exc:
        return {"published": False, "publish_error": str(exc)}


def _validate_model_stem(model_stem: str) -> None:
    from app.models_missing import load_configs

    if model_stem not in load_configs():
        raise QueueValidationError(f"no configs/models/{model_stem}.yaml -- not a known model")


def list_entries() -> list[dict]:
    return _load()["entries"]


def add_entry(
    model_stem: str,
    quants: list[str] | None = None,
    force_hf_overwrite: bool = False,
    note: str | None = None,
) -> dict:
    _validate_model_stem(model_stem)
    data = _load()
    next_id = max((e["id"] for e in data["entries"]), default=0) + 1
    entry = {
        "id": next_id,
        "model_stem": model_stem,
        "quants": quants,
        "force_hf_overwrite": force_hf_overwrite,
        "status": "pending",
        "note": note,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    data["entries"].append(entry)
    _save(data)
    entry.update(_publish())
    return entry


def update_entry(
    entry_id: int,
    status: str | None = None,
    quants: list[str] | None = None,
    force_hf_overwrite: bool | None = None,
    note: str | None = None,
) -> dict:
    if status is not None and status not in VALID_STATUSES:
        raise QueueValidationError(f"status must be one of {VALID_STATUSES}, got {status!r}")

    data = _load()
    for entry in data["entries"]:
        if entry["id"] == entry_id:
            if status is not None:
                entry["status"] = status
            if quants is not None:
                entry["quants"] = quants
            if force_hf_overwrite is not None:
                entry["force_hf_overwrite"] = force_hf_overwrite
            if note is not None:
                entry["note"] = note
            _save(data)
            entry.update(_publish())
            return entry
    raise QueueValidationError(f"no queue entry with id {entry_id}")


def delete_entry(entry_id: int) -> dict:
    data = _load()
    remaining = [e for e in data["entries"] if e["id"] != entry_id]
    if len(remaining) == len(data["entries"]):
        raise QueueValidationError(f"no queue entry with id {entry_id}")
    data["entries"] = remaining
    _save(data)
    return _publish()
