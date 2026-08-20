"""Upstream source-repo details for every buildable model series (dataset
#8, app.hf_datasets; PRD: /models_src_details, /models_src_details/update).

Each configs/models/*.yaml declares hf_model_name: the *upstream* source repo
(e.g. "black-forest-labs/FLUX.1-dev") that MFlux quantizes from -- distinct
from models_hf.json, which tracks OUR published mflux-community/{slug}-mflux-*
repos. Neither models_hf.json nor models_mflux.json carries the source repo's
total size, latest commit hash/date, or text-encoder architecture, so the
Models page's GB/Text Encoder columns have been blank -- this scans the
source repo directly via the HF API to fill that gap.

Text encoder detection: diffusers-style repos publish a model_index.json
mapping component name -> [library, class_name] (e.g. "text_encoder":
["transformers", "CLIPTextModel"]); some pipelines (FLUX) have two
(text_encoder + text_encoder_2, CLIP + T5). Repos without a diffusers
model_index.json (e.g. apple/DepthPro) get text_encoder: null, not an
error -- same "gap over guess" pattern already used in app/models_hf.py and
app/models_missing.py for fields the API can't actually answer.

size_text_encoder / size_transformers: summed from the file listing's
top-level folder ("text_encoder*/", "transformer*/", per the same
model_index.json component-folder convention) -- 0.0 when the repo genuinely
has no such folder, not None, since that's a known answer rather than a gap.

readme_meta: the repo's full README YAML frontmatter, as huggingface_hub
already parses it into ModelInfo.card_data during model_info() -- no extra
fetch required.
"""

import json
import os
import tempfile
from pathlib import Path

DATA_PATH = Path(
    os.environ.get(
        "MODELS_SRC_DETAILS_PATH",
        Path(__file__).resolve().parent.parent / "data-hf-sync" / "models_src_details.json",
    )
)


def _model_size_bytes(info) -> int:
    return sum(
        sibling.size or 0
        for sibling in info.siblings or []
        if sibling.size is not None
    )


def _component_size_bytes(info, prefix: str) -> int:
    """Total size of every sibling file whose top-level path segment starts
    with `prefix` -- diffusers-style repos put each pipeline component (per
    model_index.json) in its own folder, e.g. "text_encoder/", "text_encoder_2/",
    "transformer/". Returns 0 (not None) when the repo has no such folder --
    that's a real, known answer ("this component doesn't exist here"), not a
    gap in what we could find out."""
    total = 0
    for sibling in info.siblings or []:
        if sibling.size is None:
            continue
        top_segment = sibling.rfilename.split("/", 1)[0]
        if top_segment.startswith(prefix):
            total += sibling.size
    return total


def extract_text_encoders(model_index: dict) -> list[str] | None:
    """Pull every "text_encoder*" component's class name out of an already-
    parsed model_index.json dict, in the order they were declared (so FLUX's
    CLIP-then-T5 stays that order). None if there's nothing to extract --
    callers use that to mean "not a diffusers pipeline", not "empty list"."""
    encoders = [
        value[-1]
        for key, value in model_index.items()
        if key.startswith("text_encoder") and isinstance(value, list) and value
    ]
    return encoders or None


def _fetch_text_encoders(repo_id: str, siblings: list[str], token: str | None) -> list[str] | None:
    if "model_index.json" not in siblings:
        return None

    from huggingface_hub import hf_hub_download

    try:
        path = hf_hub_download(repo_id, "model_index.json", token=token)
        model_index = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing/malformed index just means "unknown", not a failed scan
        return None

    return extract_text_encoders(model_index)


def _repo_entry(api, repo_id: str, token: str | None) -> dict:
    info = api.model_info(repo_id, files_metadata=True)
    siblings = [s.rfilename for s in info.siblings or []]
    return {
        "hf_model_name": repo_id,
        "size_gb": round(_model_size_bytes(info) / 1_000_000_000, 2),
        "size_text_encoder": round(_component_size_bytes(info, "text_encoder") / 1_000_000_000, 2),
        "size_transformers": round(_component_size_bytes(info, "transformer") / 1_000_000_000, 2),
        "commit_hash": info.sha,
        "last_modified": info.last_modified.isoformat() if info.last_modified else None,
        "text_encoder": _fetch_text_encoders(repo_id, siblings, token),
        # The repo's full README YAML frontmatter (license, tags, base_model,
        # etc.) -- huggingface_hub already parses this into card_data as part
        # of model_info(), no extra fetch needed. None if the repo has no
        # README/card metadata at all.
        "readme_meta": info.card_data.to_dict() if info.card_data else None,
    }


def scan_models_src_details(configs: dict[str, dict] | None = None) -> dict:
    """{config_stem: {...}} for every configs/models/*.yaml with an
    hf_model_name. One HF API call (+ a model_index.json fetch, when present)
    per distinct source repo -- several config stems can share one upstream
    repo (e.g. differently-quantized variants of the same source), so repos
    are only fetched once and reused. A per-repo failure (gated repo, network
    hiccup, renamed repo) is recorded as {"hf_model_name": ..., "error": str}
    for that stem rather than aborting the whole scan."""
    from huggingface_hub import HfApi

    from app.models_missing import load_configs

    configs = configs if configs is not None else load_configs()
    token = os.environ.get("HF_TOKEN")
    api = HfApi(token=token)

    seen_repos: dict[str, dict] = {}
    result: dict[str, dict] = {}
    for stem, config in configs.items():
        repo_id = config.get("hf_model_name")
        if not repo_id:
            continue
        if repo_id not in seen_repos:
            try:
                seen_repos[repo_id] = _repo_entry(api, repo_id, token)
            except Exception as exc:  # noqa: BLE001 - one bad repo shouldn't abort the scan
                seen_repos[repo_id] = {"hf_model_name": repo_id, "error": str(exc)}
        result[stem] = seen_repos[repo_id]

    return result


def write_models_src_details(data: dict, data_path: Path | None = None) -> None:
    data_path = data_path or DATA_PATH
    data_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=data_path.parent, delete=False
    ) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(data_path)


def load_models_src_details(data_path: Path | None = None) -> dict:
    data_path = data_path or DATA_PATH
    if not data_path.exists():
        return {}
    try:
        with open(data_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # Same reasoning as app.models_hf.load_models_hf: corrupt data should
        # degrade to "nothing scanned yet", not 500 the whole endpoint.
        return {}


def refresh_models_src_details() -> dict:
    """Scan every configured model's upstream source repo, write
    data-hf-sync/models_src_details.json, and publish it to the HF bucket."""
    from app.hf_datasets import push

    data = scan_models_src_details()
    write_models_src_details(data)
    push("models_src_details")
    return data
