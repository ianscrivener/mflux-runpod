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


class QueueStorageError(RuntimeError):
    """The queue file exists but is unreadable/corrupt/wrong-shaped in a way
    that isn't the known not-yet-initialized placeholder. Unlike the other
    HF-synced datasets, models_queue.json is human-authored and can't be
    regenerated -- silently treating a corrupt file as "empty" (the pattern
    copied from app.models_hf's genuinely-regenerable cache) would let the
    next add_entry() overwrite it with a single new entry, permanently
    losing everything else that was in it. Refuse instead."""


_PLACEHOLDER_STUB = {"place": "holder"}


def _load() -> dict:
    if not LOCAL_PATH.exists():
        return {"entries": []}
    try:
        data = json.loads(LOCAL_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueStorageError(f"{LOCAL_PATH} exists but isn't readable/valid JSON: {exc}") from exc
    if data == _PLACEHOLDER_STUB:
        return {"entries": []}
    if "entries" not in data or not isinstance(data["entries"], list):
        raise QueueStorageError(
            f"{LOCAL_PATH} doesn't have the expected {{'entries': [...]}} shape and "
            "isn't the known placeholder stub -- refusing to silently treat this as "
            "an empty queue"
        )
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
    thing the caller actually asked for, so a durable-publish failure must
    not fail the whole CRUD call -- confirmed live, 2026-08-19: an
    unconfigured DO_SPACES_* turned every add/update/delete into a raw 500
    despite the local file being written correctly. Catches broadly, not
    just the two "not configured at all" error classes -- wrong credentials,
    a network blip, or an HF/DO API error would otherwise hit this exact
    same failure mode."""
    import logging

    from app.queue_store import publish

    try:
        publish()
        return {"published": True}
    except Exception as exc:  # noqa: BLE001 - any publish failure degrades, never raises
        logging.getLogger(__name__).warning("queue publish failed: %s", exc)
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


UNSET = object()
"""Sentinel distinguishing 'field omitted' from 'field explicitly set to
None' in update_entry -- plain None can't do this, since None is also the
legitimate value for clearing quants/note back to null. Route layers should
pass request.model_dump(exclude_unset=True) so an omitted JSON field never
reaches update_entry at all, rather than arriving as None."""


def update_entry(
    entry_id: int,
    status=UNSET,
    quants=UNSET,
    force_hf_overwrite=UNSET,
    note=UNSET,
) -> dict:
    if status is not UNSET and status is not None and status not in VALID_STATUSES:
        raise QueueValidationError(f"status must be one of {VALID_STATUSES}, got {status!r}")

    data = _load()
    for entry in data["entries"]:
        if entry["id"] == entry_id:
            if status is not UNSET:
                entry["status"] = status
            if quants is not UNSET:
                entry["quants"] = quants
            if force_hf_overwrite is not UNSET:
                entry["force_hf_overwrite"] = force_hf_overwrite
            if note is not UNSET:
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
