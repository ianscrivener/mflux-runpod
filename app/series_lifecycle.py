"""Per-model-series build lifecycle helpers (PRD Storage section; tasks 5/12/13).

Ties together the pieces built separately in models_missing.py (what's
missing) and runner.py (the actual build+upload). This module owns:

  - downloading a series' source weights into local build scratch space
    (mirrors the old cpu-admin-hf-cache.py, but scoped to one series instead
    of a shared manifest-driven cache)
  - deleting those source weights once every quant for the series is built
    (frees the large upstream safetensors while build artifacts may still be
    mid-flight for other quants)
  - deciding whether a series is fully published: only once every quant in
    its config is confirmed live on HF

None of this provisions or deletes anything by itself when imported — callers
(the Orchestrator's /generate handlers, or a worker-context script with the
build scratch space actually mounted) decide when to invoke it.

NOTE: this previously also owned tearing down the series' ephemeral RunPod
Network Volume once series_is_complete() went true (teardown_if_complete()).
That was removed while migrating off RunPod -- it had no production caller
yet (the Runner->Orchestrator report callback never wired it up), only tests.
series_is_complete() itself is unchanged and still the right check to reuse
once a real teardown step exists for whatever the new worker's build scratch
space turns out to be.
"""

from pathlib import Path

SOURCE_WEIGHTS_DIRNAME = "source"


def source_weights_path(volume_root: Path) -> Path:
    return volume_root / SOURCE_WEIGHTS_DIRNAME


def download_source_weights(hf_model_name: str, volume_root: Path) -> Path:
    """Download a series' upstream HF weights into its mounted volume.

    Must run in a context where the volume is actually mounted (a Runner pod),
    not from the Orchestrator's own container. Idempotent: huggingface_hub's
    snapshot_download resumes/reuses an existing local snapshot.
    """
    from huggingface_hub import snapshot_download

    dest = source_weights_path(volume_root)
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=hf_model_name, local_dir=str(dest))
    return dest


def delete_source_weights(volume_root: Path) -> None:
    """Drop the large downloaded source weights once every quant for a series
    has been built from them. Build artifacts (already-uploaded or in-flight
    quant directories) are untouched — only the `source/` subdirectory."""
    import shutil

    dest = source_weights_path(volume_root)
    if dest.exists():
        shutil.rmtree(dest)


def series_is_complete(config: dict, hf_manifest: dict) -> bool:
    """A series is complete only when every quant declared in its config has a
    live repo on HF — same rule as models_missing.compute_missing()."""
    from app.models_missing import expected_repo_ids

    published = {m["model_name"] for m in hf_manifest.get("hf_models", [])}
    repo_ids = expected_repo_ids(config)
    return all(repo_id in published for repo_id in repo_ids.values())
