"""GPU Runner (PRD: (2) Runner - GPU).

Ports the old create-mflux-models.py logic (Modal A10 script) to run against
the worker's local build scratch space instead of a Modal Volume. Previously
that scratch space was a RunPod Network Volume; the RunPod-specific dispatch
layer was removed while migrating to a different GPU worker (see
app/generate.py::dispatch_trigger) -- this module's own logic is unaffected,
since it never talked to RunPod directly, only to a local path. No @Endpoint
decorator here — this module is plain, testable Python.

One GPU job builds and uploads exactly ONE quant (decided together with the
user: better crash-isolation and retry granularity than one job per series,
and mflux quant builds are minutes-long so per-job cold start is negligible
relative to build time). build_and_upload_one_quant():
  1. Locates the mflux model class named in the config (model_object) under
     mflux.models, and builds the ModelConfig named in model_config.
  2. Builds the quant into the series' build scratch space, reusing a valid
     local build via the sha256 manifest.json check (crash-resume, task 13)
     — unless it's already on HF and force_hf_overwrite is false, in which
     case it's skipped entirely.
  3. Uploads the built quant to its own repo (mflux-community/{slug}-mflux-{quant}).
  4. Deletes the local build from the volume once uploaded.
  5. Adds the uploaded repo to the series' HF Collection (create-or-reuse) —
     safe to call once per quant since create_collection/add_collection_item
     are both exists_ok idempotent; no cross-job coordination needed.

mflux/huggingface_hub are only imported inside functions that need them, so this
module can be imported and unit-tested (with a fake HfApi/model class) without a
GPU or the mflux package installed.
"""

import hashlib
import json
import shutil
import time
from pathlib import Path

from app.models_missing import expected_repo_ids, slugify

HF_ORG = "mflux-community"


CHUNK_SIZE = 8 * 1024 * 1024  # 8MB — avoid loading multi-GB weight files fully into memory


def hash_dir(path: Path) -> str:
    """sha256 over every file's relative path + content, for integrity checks."""
    h = hashlib.sha256()
    for f in sorted(path.rglob("*")):
        if f.is_file() and f.name != "manifest.json":
            h.update(f.relative_to(path).as_posix().encode())
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
                    h.update(chunk)
    return h.hexdigest()


def is_locally_valid(build_path: Path) -> bool:
    """Crash-resume check: a local build is reusable only if its recorded
    sha256 still matches the directory's actual contents. A missing, truncated,
    or corrupt manifest.json (e.g. from a crash mid-write) is treated as
    invalid rather than raising, so the caller falls back to a rebuild."""
    manifest_path = build_path / "manifest.json"
    if not build_path.is_dir() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return manifest.get("sha256") == hash_dir(build_path)


def find_model_class(model_object: str):
    """Locate the mflux model class named in a config's model_object field,
    searching mflux.models submodules (matches create-mflux-models.py)."""
    import importlib
    import pkgutil

    import mflux.models as mflux_models  # type: ignore

    for _, module_name, _ in pkgutil.walk_packages(
        mflux_models.__path__, mflux_models.__name__ + "."
    ):
        module = importlib.import_module(module_name)
        if hasattr(module, model_object):
            return getattr(module, model_object)
    raise ImportError(f"Could not find model class {model_object!r} under mflux.models")


def resolve_model_config(model_config_name: str):
    """Build the mflux ModelConfig named in a config's model_config field."""
    from mflux.models.common.config import ModelConfig  # type: ignore

    return getattr(ModelConfig, model_config_name)()


def build_quant(
    model_cls,
    model_config_obj,
    quant: str,
    build_path: Path,
) -> None:
    """Build one quantized model to build_path and write its manifest.json."""
    quantize = None if quant == "bf16" else int(quant.removeprefix("q"))
    model = model_cls(quantize=quantize, model_config=model_config_obj)
    model.save_model(str(build_path))
    (build_path / "manifest.json").write_text(json.dumps({"sha256": hash_dir(build_path)}))


def upload_quant(api, repo_id: str, build_path: Path, force_hf_overwrite: bool) -> None:
    repo_exists = api.repo_exists(repo_id=repo_id)
    if repo_exists and force_hf_overwrite:
        api.delete_repo(repo_id=repo_id)
    api.create_repo(repo_id=repo_id, exist_ok=True, private=False)

    from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError  # noqa: F401

    for attempt in range(3):
        try:
            api.upload_folder(repo_id=repo_id, folder_path=str(build_path))
            break
        except TimeoutError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)


def build_and_upload_one_quant(
    config: dict,
    quant: str,
    volume_root: Path,
    force_hf_overwrite: bool = False,
    already_published: bool = False,
    api=None,
) -> dict:
    """Build+upload a single quant (one GPU job = one quant). Returns a result
    dict: {"quant", "repo_id", "status"} where status is one of
    "skipped_existing" | "uploaded". Also adds the repo to the series' HF
    Collection (idempotent — safe even if other quants' jobs do the same
    concurrently).

    volume_root is the mounted per-model-series volume's local path — build
    artifacts live at volume_root/{slug}-mflux-{quant}/.
    """
    if api is None:
        from huggingface_hub import HfApi

        api = HfApi()

    slug = slugify(config["collection"]["name"])
    repo_ids = expected_repo_ids(config)
    repo_id = repo_ids[quant]

    if already_published and not force_hf_overwrite:
        result = {"quant": quant, "repo_id": repo_id, "status": "skipped_existing"}
        ensure_collection(api, config, [repo_id])
        return result

    model_cls = find_model_class(config["model_object"])
    model_config_obj = resolve_model_config(config["model_config"])

    build_path = volume_root / f"{slug}-mflux-{quant}"
    if not is_locally_valid(build_path):
        if build_path.exists():
            shutil.rmtree(build_path)
        build_path.mkdir(parents=True, exist_ok=True)
        build_quant(model_cls, model_config_obj, quant, build_path)

    upload_quant(api, repo_id, build_path, force_hf_overwrite)
    shutil.rmtree(build_path)

    result = {"quant": quant, "repo_id": repo_id, "status": "uploaded"}
    ensure_collection(api, config, [repo_id])
    return result


def ensure_collection(api, config: dict, repo_ids: list[str]) -> None:
    """Create-or-reuse an HF Collection grouping every uploaded quant repo."""
    collection = config["collection"]
    created = api.create_collection(
        title=collection["name"],
        description=collection.get("description", ""),
        namespace=HF_ORG,
        private=False,
        exists_ok=True,
    )
    for repo_id in repo_ids:
        api.add_collection_item(
            collection_slug=created.slug,
            item_id=repo_id,
            item_type="model",
            exists_ok=True,
        )
