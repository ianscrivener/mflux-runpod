"""Durable master copy of models_queue.json, in the GPU worker's own HF bucket.

2026-08-19 design: models_queue.json is human-authored, curated state -- unlike
the other seven datasets it can't be regenerated from anything else, so it needs
a real source of truth, not just a cache. The HF bucket (hf://buckets/
mflux-community/ci, see app.hf_datasets) is shared/write-restricted to three
MFlux admins but not private, and its security boundary is still an open
question (a follow-up security-audit item per the user). Rather than block on
that, the actual master lives here -- the HF bucket copy is a throwaway
mirror, not authoritative. If this bucket is unreachable, the local
data-hf-sync/models_queue.json file and the HF mirror are still readable,
just possibly stale.

NOTE (2026-08-20): originally a dedicated DigitalOcean Spaces bucket. Moved
onto the GPU worker's own companion bucket
(cleverheart2026/mflux-model-gpu-runner-storage) via huggingface_hub's
bucket API, same move already made for app.outbox and for the same reason --
one HF_TOKEN credential instead of a second cloud account, reusing a bucket
that's already paid for as this Space's Persistent Storage. `queue/` here is
a third sibling prefix, alongside outbox's `results/` and the worker's own
`build/`.
"""

import tempfile
from pathlib import Path

QUEUE_KEY = "queue/models_queue.json"
LOCAL_PATH = Path(__file__).resolve().parent.parent / "data-hf-sync" / "models_queue.json"
DEFAULT_BUCKET_ID = "cleverheart2026/mflux-model-gpu-runner-storage"


class QueueStoreConfigError(RuntimeError):
    """Raised when HF_TOKEN isn't configured -- same pattern as
    app.outbox.OutboxConfigError."""


def _bucket_id() -> str:
    import os

    return os.environ.get("QUEUE_STORE_BUCKET_ID") or DEFAULT_BUCKET_ID


def _token() -> str:
    import os

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise QueueStoreConfigError(
            "models_queue HF bucket master requires HF_TOKEN in the "
            "environment, but it's not set."
        )
    return token


def save_master(local_path: Path = LOCAL_PATH) -> None:
    """Upload the local queue file to the HF bucket as the new master."""
    import huggingface_hub as hf

    token = _token()  # fail on missing config before touching the local file
    with open(local_path, "rb") as f:
        body = f.read()
    hf.batch_bucket_files(_bucket_id(), add=[(body, QUEUE_KEY)], token=token)


def load_master(local_path: Path = LOCAL_PATH) -> bytes:
    """Download the HF bucket master, overwriting the local file. Returns the
    bytes written, for callers that want to confirm content."""
    import huggingface_hub as hf

    with tempfile.TemporaryDirectory() as tmp_dir:
        remote_path = Path(tmp_dir) / "models_queue.json"
        hf.download_bucket_files(_bucket_id(), [(QUEUE_KEY, remote_path)], token=_token())
        body = remote_path.read_bytes()

    local_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=local_path.parent, delete=False) as tmp:
        tmp.write(body)
        tmp_path = Path(tmp.name)
    tmp_path.replace(local_path)

    return body


def publish() -> dict:
    """Save the local file as the new HF-bucket master, then also refresh the
    throwaway HF-dataset-bucket mirror. This is what a human editing
    models_queue.json locally should call to make their edit durable."""
    from app.hf_datasets import push

    save_master()
    mirror = push("models_queue")
    return {"master": "hf_bucket", "mirror": mirror}


def restore() -> dict:
    """Pull the HF-bucket master down over the local file -- use after a
    fresh checkout/volume, or to discard local edits and revert to master."""
    load_master()
    return {"restored_from": "hf_bucket", "local_path": str(LOCAL_PATH)}
