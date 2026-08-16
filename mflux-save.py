"""Build mflux quantizations locally, upload them to Hugging Face, and group them in a collection.

Usage:
    uv run python mflux-save.py --config-path configs/Fibo.yaml
"""

import argparse
import hashlib
import importlib
import json
import os
import pkgutil
import re
import shutil
import time
from pathlib import Path

import yaml
from huggingface_hub import HfApi

ORG = "mflux-community"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def hash_dir(path: Path) -> str:
    """Return a sha256 of every build file except its integrity manifest."""
    digest = hashlib.sha256()
    for file_path in sorted(path.rglob("*")):
        if file_path.is_file() and file_path.name != "manifest.json":
            digest.update(file_path.relative_to(path).as_posix().encode())
            digest.update(file_path.read_bytes())
    return digest.hexdigest()


def find_model_class(model_object: str):
    import mflux.models as mflux_models  # type: ignore

    for _, module_name, _ in pkgutil.walk_packages(
        mflux_models.__path__, mflux_models.__name__ + "."
    ):
        module = importlib.import_module(module_name)
        if hasattr(module, model_object):
            return getattr(module, model_object)
    raise ImportError(f"Could not find model class {model_object!r} under mflux.models")


def save_models(config: dict, force: bool = False) -> None:
    from mflux.models.common.config import ModelConfig  # type: ignore

    api = HfApi()
    model_cls = find_model_class(config["model_object"])
    model_config = getattr(ModelConfig, config["model_config"])()
    variant_slug = slugify(config["collection"]["name"])
    repo_ids = []

    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    build_cache_root = hf_home / "mflux-builds"
    build_cache_root.mkdir(parents=True, exist_ok=True)

    for quant in config["quants"]:
        path_name = f"{variant_slug}-mflux-{quant}"
        repo_id = f"{ORG}/{path_name}"
        cached_path = build_cache_root / path_name

        repo_exists = api.repo_exists(repo_id=repo_id)
        if repo_exists and not force:
            print(f"[{path_name}] already exists on the Hub, skipping")
            repo_ids.append(repo_id)
            continue

        local_valid = False
        manifest_path = cached_path / "manifest.json"
        if cached_path.is_dir() and manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            local_valid = manifest.get("sha256") == hash_dir(cached_path)
            if not local_valid:
                shutil.rmtree(cached_path)

        if local_valid:
            print(f"[{path_name}] reusing valid local build")
        else:
            print(f"[{path_name}] no valid local cache, building...")
            quantize = None if quant == "bf16" else int(quant.removeprefix("q"))
            model = model_cls(quantize=quantize, model_config=model_config)
            model.save_model(str(cached_path))
            manifest_path.write_text(json.dumps({"sha256": hash_dir(cached_path)}))

        if repo_exists:
            print(f"[{path_name}] --force replacing existing Hub repository")
            api.delete_repo(repo_id=repo_id)
        api.create_repo(repo_id=repo_id, exist_ok=True, private=False)
        for attempt in range(3):
            try:
                api.upload_folder(repo_id=repo_id, folder_path=str(cached_path))
                break
            except TimeoutError:
                if attempt == 2:
                    raise
                delay_seconds = 2**attempt
                print(f"[{path_name}] upload timed out; retrying in {delay_seconds}s")
                time.sleep(delay_seconds)
        repo_ids.append(repo_id)
        shutil.rmtree(cached_path)

    collection = config["collection"]
    created = api.create_collection(
        title=collection["name"],
        description=collection.get("description", ""),
        namespace=ORG,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", required=True, type=Path)
    parser.add_argument("--force", action="store_true", help="Replace existing Hub repositories.")
    args = parser.parse_args()

    config = yaml.safe_load(args.config_path.read_text())
    save_models(config, force=args.force)


if __name__ == "__main__":
    main()