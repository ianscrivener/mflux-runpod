"""Durable master copy of models_queue.json in DigitalOcean Spaces.

2026-08-19 design: models_queue.json is human-authored, curated state -- unlike
the other six datasets it can't be regenerated from anything else, so it needs
a real source of truth, not just a cache. The HF bucket (hf://buckets/
mflux-community/ci, see app.hf_datasets) is shared/write-restricted to three
MFlux admins but not private, and its security boundary is still an open
question (a follow-up security-audit item per the user). Rather than block on
that, the actual master lives here in DO Spaces (the same bucket/credentials
app.outbox already uses for the Runner result outbox, under a different key
prefix so the two never collide) -- the HF bucket copy is a throwaway mirror,
not authoritative. If DO Spaces is unreachable, the local data-hf-sync/models_queue.json
file and the HF mirror are still readable, just possibly stale.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

QUEUE_KEY = "queue/models_queue.json"
LOCAL_PATH = Path(__file__).resolve().parent.parent / "data-hf-sync" / "models_queue.json"

_REQUIRED_ENV = ("DO_SPACES_KEY", "DO_SPACES_SECRET", "DO_SPACES_ENDPOINT", "DO_SPACES_BUCKET")


class QueueStoreConfigError(RuntimeError):
    """Raised when DO_SPACES_* isn't configured -- same pattern as
    app.outbox.OutboxConfigError."""


def _region_endpoint(raw_endpoint: str) -> str:
    # Identical to app.outbox._region_endpoint -- both talk to the same DO
    # Spaces account, kept as separate small functions rather than a shared
    # import to match this project's existing convention of each storage
    # module owning its own client setup (see app.outbox, app.runpod_volumes).
    host = urlparse(raw_endpoint if "://" in raw_endpoint else f"https://{raw_endpoint}").netloc
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2:] == ["digitaloceanspaces", "com"]:
        return f"https://{parts[-3]}.digitaloceanspaces.com"
    return raw_endpoint


def _client():
    import boto3

    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise QueueStoreConfigError(
            f"models_queue DO Spaces master requires {', '.join(missing)} in the "
            "environment, but not set."
        )
    endpoint = _region_endpoint(os.environ["DO_SPACES_ENDPOINT"])
    region = os.environ.get("DO_SPACES_REGION") or endpoint.split("//", 1)[1].split(".", 1)[0]
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=os.environ["DO_SPACES_KEY"],
        aws_secret_access_key=os.environ["DO_SPACES_SECRET"],
    )
    return client, os.environ["DO_SPACES_BUCKET"]


def save_master(local_path: Path = LOCAL_PATH) -> None:
    """Upload the local queue file to DO Spaces as the new master."""
    client, bucket = _client()
    with open(local_path, "rb") as f:
        client.put_object(Bucket=bucket, Key=QUEUE_KEY, Body=f.read(), ContentType="application/json")


def load_master(local_path: Path = LOCAL_PATH) -> bytes:
    """Download the DO Spaces master, overwriting the local file. Returns the
    bytes written, for callers that want to confirm content."""
    import tempfile

    client, bucket = _client()
    obj = client.get_object(Bucket=bucket, Key=QUEUE_KEY)
    body = obj["Body"].read()

    local_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=local_path.parent, delete=False) as tmp:
        tmp.write(body)
        tmp_path = Path(tmp.name)
    tmp_path.replace(local_path)

    return body


def publish() -> dict:
    """Save the local file as the new DO Spaces master, then also refresh the
    throwaway HF-bucket mirror. This is what a human editing models_queue.json
    locally should call to make their edit durable."""
    from app.hf_datasets import push

    save_master()
    mirror = push("models_queue")
    return {"master": "do_spaces", "mirror": mirror}


def restore() -> dict:
    """Pull the DO Spaces master down over the local file -- use after a
    fresh checkout/volume, or to discard local edits and revert to master."""
    load_master()
    return {"restored_from": "do_spaces", "local_path": str(LOCAL_PATH)}
