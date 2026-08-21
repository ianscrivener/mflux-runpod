"""Durable Runner -> Orchestrator result delivery via a Hugging Face bucket,
replacing the direct HTTP callback dockerFiles/runner_handler.py used to POST
straight to ORCHESTRATOR_BASE_URL.

That required the Orchestrator to be reachable at the exact moment a job
finished -- fine for an always-on cloud host, but not for one that might be
offline for hours (e.g. a personal machine, per 2026-08-18's design
discussion). Design: a worker adds a small JSON result object per quant job
to a well-known key; the Orchestrator polls (list + process + delete) on
its own schedule, whenever it happens to be running. A result sitting
unprocessed in the bucket for hours or days is completely fine -- that's the
entire point.

Bucket: originally DigitalOcean Spaces (S3-compatible, boto3); replaced
2026-08-20 with the HF Spaces GPU worker's own companion bucket
(`cleverheart2026/mflux-model-gpu-runner-storage`) via huggingface_hub's
bucket API. Confirmed live that this bucket is the worker Space's own
Persistent Storage backing -- the same bucket mounted at /data inside the
worker container for build scratch (see docker-runner-hf/worker.py's
BUILD_ROOT), so `results/` here is a sibling prefix to `build/`, not a
separate resource. Only credential needed either side is HF_TOKEN, which
every other piece of this project already requires (model uploads, the
private Space's own access gate) -- no second cloud account/credential set
to configure, unlike the DO Spaces version this replaces.

Object key layout: results/{run_id}/{quant}.json
"""

import json
import os
import tempfile
from pathlib import Path

RESULTS_PREFIX = "results/"
DEFAULT_BUCKET_ID = "cleverheart2026/mflux-model-gpu-runner-storage"


class OutboxConfigError(RuntimeError):
    """Raised when HF_TOKEN isn't configured in the environment -- a
    deployment/config problem, not a bad request. Callers (HTTP routes)
    should catch this and return a clean error response instead of a bare
    500/crash, same pattern as app.generate.DispatchConfigError."""


def _bucket_id() -> str:
    """Overridable via OUTBOX_BUCKET_ID for anyone pointing this at a
    different bucket; defaults to the worker Space's own companion bucket,
    the actual bucket in use as of 2026-08-20."""
    return os.environ.get("OUTBOX_BUCKET_ID") or DEFAULT_BUCKET_ID


def _token() -> str:
    # .strip() guards against a pasted-in Space secret carrying a trailing
    # newline/whitespace -- confirmed live 2026-08-21: an unstripped token
    # with a stray character reached huggingface_hub's bucket-upload session
    # as the literal Authorization header value, which raised a bare
    # "ValueError: Invalid header value... failed to parse header value"
    # instead of a clean auth error.
    token = (os.environ.get("HF_TOKEN") or "").strip()
    if not token:
        raise OutboxConfigError(
            "HF bucket outbox requires HF_TOKEN in the environment, but it's "
            "not set -- durable result delivery isn't configured."
        )
    return token


def put_result(run_id: int, quant: str, payload: dict) -> str:
    """Durably deposit one quant job's result. Returns the object key."""
    import huggingface_hub as hf

    key = f"{RESULTS_PREFIX}{run_id}/{quant}.json"
    hf.batch_bucket_files(
        _bucket_id(),
        add=[(json.dumps(payload).encode("utf-8"), key)],
        token=_token(),
    )
    return key


def list_pending() -> list[str]:
    """List every pending result object key. Bucket listing order isn't
    upload-time-ordered, but processing order doesn't matter here.

    list_bucket_tree(recursive=True) yields BucketFolder entries (e.g. the
    per-run_id subdirectory) alongside BucketFile ones -- both have a
    `.path`, so without filtering, a folder path could reach get_result()
    downstream and fail there instead of being excluded here."""
    import huggingface_hub as hf

    return [
        f.path
        for f in hf.list_bucket_tree(
            _bucket_id(), prefix=RESULTS_PREFIX, recursive=True, token=_token()
        )
        if f.type == "file"
    ]


def get_result(key: str) -> dict:
    import huggingface_hub as hf

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_path = Path(tmp_dir) / "result.json"
        hf.download_bucket_files(_bucket_id(), [(key, local_path)], token=_token())
        return json.loads(local_path.read_text())


def delete_result(key: str) -> None:
    import huggingface_hub as hf

    hf.batch_bucket_files(_bucket_id(), delete=[key], token=_token())


def process_pending() -> dict:
    """Process every pending result: apply it to the runs/quant_builds
    tables (same effect the old HTTP callback route had), then delete the
    object. A malformed/unparseable object is left in place (not deleted)
    so it can be inspected rather than silently lost -- reported in
    `errors` instead. run_id is parsed from the key itself so even a
    malformed payload still identifies which run to flag."""
    from huggingface_hub.errors import HfHubHTTPError

    from app.report import add_quant_build, is_run_deleted, update_run_status_from_children

    try:
        pending = list_pending()
    except HfHubHTTPError as exc:
        # Credentials are "set" (OutboxConfigError doesn't fire) but wrong,
        # or the bucket doesn't exist/isn't reachable with this token --
        # convert to the same clean, catchable error either way rather than
        # letting a raw huggingface_hub exception surface as a bare 500.
        raise OutboxConfigError(f"HF bucket outbox request failed: {exc}") from exc

    processed = []
    errors = []
    for key in pending:
        try:
            payload = get_result(key)
            run_id = int(key.removeprefix(RESULTS_PREFIX).split("/")[0])
            if is_run_deleted(run_id):
                # The run was explicitly deleted (see report.delete_run)
                # after this job was dispatched -- there's nothing left to
                # apply this result to. Discard the object same as a
                # normally-applied one, without inserting an orphaned
                # quant_builds row for a run that no longer exists.
                delete_result(key)
                processed.append(key)
                continue
            for qb in payload.get("quant_builds", []):
                add_quant_build(
                    run_id,
                    qb["quant"],
                    status=qb.get("status", "failed"),
                    total_size_bytes=qb.get("total_size_bytes"),
                    text_encoder_bytes=qb.get("text_encoder_bytes"),
                    transformer_bytes=qb.get("transformer_bytes"),
                    vae_bytes=qb.get("vae_bytes"),
                    build_duration_s=qb.get("build_duration_s"),
                    upload_duration_s=qb.get("upload_duration_s"),
                    hf_repo_id=qb.get("hf_repo_id"),
                )
            update_run_status_from_children(
                run_id, finished_at=payload["finished_at"], error=payload.get("error")
            )
            delete_result(key)
            processed.append(key)
        except Exception as exc:  # noqa: BLE001 - one bad object shouldn't stop the batch
            errors.append({"key": key, "error": str(exc)})
    return {"processed": processed, "errors": errors}
