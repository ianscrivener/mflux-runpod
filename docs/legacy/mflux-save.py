"""Build mflux quantizations locally, upload them to Hugging Face, and group them in a collection.

Usage:
    uv run python mflux-save.py --config-path configs/Fibo.yaml
    uv run python mflux-save.py --config-path configs/Fibo.yaml --push-readme
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from huggingface_hub import HfApi

ORG = "mflux-community"
SPACE_ID = f"{ORG}/README"
QUANTS = ["Q3", "Q4", "Q6", "Q8", "BF16"]
HERE = Path(__file__).parent


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


def verify_upload(api: HfApi, repo_id: str, cached_path: Path) -> None:
    """Confirm every local build file actually landed on the Hub before it's deleted."""
    local_files = {
        f.relative_to(cached_path).as_posix()
        for f in cached_path.rglob("*")
        if f.is_file() and f.name != "manifest.json"
    }
    remote_files = set(api.list_repo_files(repo_id=repo_id))
    missing = local_files - remote_files
    if missing:
        raise RuntimeError(
            f"[{repo_id}] upload verification failed, missing on Hub: {sorted(missing)}"
        )


def upload_model(api: HfApi, path_name: str, repo_id: str, cached_path: Path, repo_exists: bool) -> str:
    """Push a built model to the Hub, verify it landed, and clean up its local cache. Runs on the upload thread."""
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

    verify_upload(api, repo_id, cached_path)
    print(f"[{path_name}] upload verified")
    shutil.rmtree(cached_path)
    return repo_id


def slug_from_model_cell(cell: str) -> tuple[str, str]:
    """Return (link_basename, display_text) for a '**[Text](url)**' style Model cell."""
    m = re.match(r"\*{0,2}\[(.+?)\]\((.+?)\)\*{0,2}", cell.strip())
    if not m:
        return "", cell.strip()
    text, url = m.group(1), m.group(2)
    basename = url.rstrip("/").rsplit("/", 1)[-1]
    return basename, text


def update_readme_table(readme: str, collection_name: str, repo_ids_by_quant: dict[str, str]) -> str:
    """Fill in quant-column links for collection_name's row, appending a new row if none matches."""
    target = re.sub(r"[^a-z0-9]", "", collection_name.lower())
    lines = readme.splitlines()

    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.split("|")]
        # a data row has a leading/trailing empty cell from split("|") plus 8 columns
        if len(cells) != 10 or not line.strip().startswith("|") or set(cells[1]) <= {"-", ":"}:
            continue
        model_cell = cells[1]
        basename, display_text = slug_from_model_cell(model_cell)
        candidates = [re.sub(r"[^a-z0-9]", "", basename.lower()), re.sub(r"[^a-z0-9]", "", display_text.lower())]
        if target not in candidates:
            continue

        quant_cells = cells[4:9]
        for qi, quant in enumerate(QUANTS):
            repo_id = repo_ids_by_quant.get(quant.lower())
            if repo_id:
                quant_cells[qi] = f"**[{quant}](https://huggingface.co/{repo_id})**"
        cells[4:9] = quant_cells
        lines[i] = "| " + " | ".join(cells[1:-1]) + " |"
        return "\n".join(lines) + "\n"

    # No matching row: append a new one.
    quant_cells = []
    for quant in QUANTS:
        repo_id = repo_ids_by_quant.get(quant.lower())
        quant_cells.append(f"**[{quant}](https://huggingface.co/{repo_id})**" if repo_id else "")
    first_repo_id = next(iter(repo_ids_by_quant.values()), "")
    model_url = f"https://huggingface.co/{first_repo_id}" if first_repo_id else ""
    new_row = (
        f"| **[{collection_name}]({model_url})** "
        f"| | | " + " | ".join(quant_cells) + " |"
    )
    lines.append(new_row)
    return "\n".join(lines) + "\n"


def push_readme_update(api: HfApi, collection_name: str, repo_ids_by_quant: dict[str, str], push: bool) -> None:
    readme_path = api.hf_hub_download(repo_id=SPACE_ID, repo_type="space", filename="README.md")
    original = Path(readme_path).read_text()
    updated = update_readme_table(original, collection_name, repo_ids_by_quant)

    out_path = HERE / "README.local.md"
    out_path.write_text(updated)
    print(f"Wrote {out_path}")

    if updated == original:
        print("README: no changes.")
        return
    if not push:
        print("README dry run (pass --push-readme to upload). Compare README.local.md.")
        return
    api.upload_file(
        path_or_fileobj=str(out_path),
        path_in_repo="README.md",
        repo_id=SPACE_ID,
        repo_type="space",
    )
    print(f"Pushed README to https://huggingface.co/spaces/{SPACE_ID}")


def save_models(config: dict, force: bool = False, push_readme: bool = False) -> None:
    from mflux.models.common.config import ModelConfig  # type: ignore

    api = HfApi()
    model_cls = find_model_class(config["model_object"])
    model_config = getattr(ModelConfig, config["model_config"])()
    variant_slug = slugify(config["collection"]["name"])
    repo_ids = []
    repo_ids_by_quant: dict[str, str] = {}

    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    build_cache_root = hf_home / "mflux-builds"
    build_cache_root.mkdir(parents=True, exist_ok=True)

    # Uploads run on a background thread so the next model can build on the
    # main thread while the previous one is still pushing to the Hub.
    with ThreadPoolExecutor(max_workers=1) as upload_pool:
        pending_uploads = []

        for quant in config["quants"]:
            path_name = f"{variant_slug}-mflux-{quant}"
            repo_id = f"{ORG}/{path_name}"
            cached_path = build_cache_root / path_name

            repo_exists = api.repo_exists(repo_id=repo_id)
            if repo_exists and not force:
                print(f"[{path_name}] already exists on the Hub, skipping")
                repo_ids.append(repo_id)
                repo_ids_by_quant[quant] = repo_id
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

            future = upload_pool.submit(
                upload_model, api, path_name, repo_id, cached_path, repo_exists
            )
            pending_uploads.append((quant, future))

        for quant, future in pending_uploads:
            repo_id = future.result()
            repo_ids.append(repo_id)
            repo_ids_by_quant[quant] = repo_id

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

    push_readme_update(api, collection["name"], repo_ids_by_quant, push=push_readme)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", required=True, type=Path)
    parser.add_argument("--force", action="store_true", help="Replace existing Hub repositories.")
    parser.add_argument(
        "--push-readme",
        action="store_true",
        help="Upload the updated org README to the Hub Space (default: dry run, writes README.local.md).",
    )
    args = parser.parse_args()

    config = yaml.safe_load(args.config_path.read_text())
    save_models(config, force=args.force, push_readme=args.push_readme)


if __name__ == "__main__":
    main()