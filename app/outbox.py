"""Durable Runner -> Orchestrator result delivery via DigitalOcean Spaces
(S3-compatible), replacing the direct HTTP callback dockerFiles/runner_handler.py
used to POST straight to ORCHESTRATOR_BASE_URL.

That required the Orchestrator to be reachable at the exact moment a job
finished -- fine for an always-on cloud host, but not for one that might be
offline for hours (e.g. a personal machine, per 2026-08-18's design
discussion). Design: the Runner PUTs a small JSON result object per quant
job to a well-known key; the Orchestrator polls (list + process + delete) on
its own schedule, whenever it happens to be running. A result sitting
unprocessed in the bucket for hours or days is completely fine -- that's the
entire point.

Object key layout: results/{run_id}/{quant}.json
"""

import json
import os
from urllib.parse import urlparse

RESULTS_PREFIX = "results/"

_REQUIRED_ENV = ("DO_SPACES_KEY", "DO_SPACES_SECRET", "DO_SPACES_ENDPOINT", "DO_SPACES_BUCKET")


class OutboxConfigError(RuntimeError):
    """Raised when DO_SPACES_* isn't configured in the environment -- a
    deployment/config problem, not a bad request. Callers (HTTP routes)
    should catch this and return a clean error response instead of a bare
    500/crash, same pattern as app.generate.DispatchConfigError."""


def _region_endpoint(raw_endpoint: str) -> str:
    """Normalize a possibly bucket-prefixed virtual-hosted endpoint
    (https://<bucket>.<region>.digitaloceanspaces.com) down to the generic
    regional one (https://<region>.digitaloceanspaces.com) -- boto3
    addresses the bucket via the bucket_name parameter on each call, not
    baked into the endpoint URL, so either form works but the generic one
    avoids any path/vhost-style addressing ambiguity."""
    host = urlparse(raw_endpoint if "://" in raw_endpoint else f"https://{raw_endpoint}").netloc
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2:] == ["digitaloceanspaces", "com"]:
        return f"https://{parts[-3]}.digitaloceanspaces.com"
    return raw_endpoint


def _config() -> dict:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise OutboxConfigError(
            f"DO Spaces outbox requires {', '.join(missing)} in the environment, "
            "but not set -- durable result delivery isn't configured."
        )
    endpoint = _region_endpoint(os.environ["DO_SPACES_ENDPOINT"])
    region = os.environ.get("DO_SPACES_REGION") or endpoint.split("//", 1)[1].split(".", 1)[0]
    return {
        "endpoint_url": endpoint,
        "region_name": region,
        "aws_access_key_id": os.environ["DO_SPACES_KEY"],
        "aws_secret_access_key": os.environ["DO_SPACES_SECRET"],
        "bucket": os.environ["DO_SPACES_BUCKET"],
    }


def _client():
    import boto3

    cfg = _config()
    client = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        region_name=cfg["region_name"],
        aws_access_key_id=cfg["aws_access_key_id"],
        aws_secret_access_key=cfg["aws_secret_access_key"],
    )
    return client, cfg["bucket"]


def put_result(run_id: int, quant: str, payload: dict) -> str:
    """Durably deposit one quant job's result. Returns the object key."""
    client, bucket = _client()
    key = f"{RESULTS_PREFIX}{run_id}/{quant}.json"
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def list_pending() -> list[str]:
    """List every pending result object key. S3 listing order isn't
    upload-time-ordered, but processing order doesn't matter here."""
    client, bucket = _client()
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=RESULTS_PREFIX):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def get_result(key: str) -> dict:
    client, bucket = _client()
    obj = client.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read())


def delete_result(key: str) -> None:
    client, bucket = _client()
    client.delete_object(Bucket=bucket, Key=key)


def process_pending() -> dict:
    """Process every pending result: apply it to the runs/quant_builds
    tables (same effect the old HTTP callback route had), then delete the
    object. A malformed/unparseable object is left in place (not deleted)
    so it can be inspected rather than silently lost -- reported in
    `errors` instead. run_id is parsed from the key itself so even a
    malformed payload still identifies which run to flag."""
    from botocore.exceptions import BotoCoreError, ClientError

    from app.report import add_quant_build, update_run_status_from_children

    try:
        pending = list_pending()
    except (ClientError, BotoCoreError) as exc:
        # Credentials are "set" (OutboxConfigError doesn't fire) but wrong --
        # e.g. an unsubstituted secret-template placeholder literal, still
        # sitting in an env var instead of its real value (confirmed live,
        # 2026-08-18, on the since-removed RunPod deployment: a `{{ ... }}`
        # placeholder because the secret hadn't been created in RunPod's
        # console yet). Convert to the same clean, catchable error either
        # way rather than letting a raw boto3 exception surface as a bare
        # 500.
        raise OutboxConfigError(f"DO Spaces outbox request failed: {exc}") from exc

    processed = []
    errors = []
    for key in pending:
        try:
            payload = get_result(key)
            run_id = int(key.removeprefix(RESULTS_PREFIX).split("/")[0])
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
