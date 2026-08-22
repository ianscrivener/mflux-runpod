"""Renders data/templates/hf-model-card.template.md against real catalog
data (PRD: GET /models_hf/card_preview, GPU page preview link).

Reuses app.models_catalog's already-computed expected_repo_ids (config stem
-> {quant: mflux-community/{slug}-mflux-{quant}}) rather than re-deriving the
publish slug here -- that's the one place the slugify(collection.name) rule
lives (see resolve_model_slug/compute_available_models docstrings), and
duplicating it here would drift the moment that rule changes.
"""

import importlib.metadata
from datetime import date
from pathlib import Path

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "data" / "templates" / "hf-model-card.template.md"
QUANTS = ["bf16", "q8", "q6", "q5", "q4", "q3"]

# No per-model step count exists anywhere in configs/models/*.yaml or the
# catalog yet -- hardcoded until that's tracked somewhere real.
DEFAULT_MODEL_STEPS = 9


def _mflux_version() -> str:
    try:
        return importlib.metadata.version("mflux")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def render_model_card(stem: str, quant: str) -> str:
    """Render the template for one published quant repo of one
    configs/models/*.yaml stem, e.g. ("Boogu-Image-Turbo", "bf16")."""
    from app.models_catalog import get_available_models, get_published_hf_manifest
    from app.models_hf import is_actually_published
    from app.models_missing import load_configs

    configs = load_configs()
    config = configs[stem]
    available = get_available_models(configs=configs)[stem]
    expected_repo_ids = available["expected_repo_ids"]

    published_by_name = {
        m["model_name"]: m
        for m in get_published_hf_manifest().get("hf_models", [])
        if is_actually_published(m)
    }

    src_repo = config["hf_model_name"]
    this_repo_id = expected_repo_ids[quant]
    this_published = published_by_name.get(this_repo_id)

    fields = {
        "model_name": (config.get("collection") or {}).get("name", stem),
        "model_quant": quant.upper(),
        "model_src": src_repo.rsplit("/", 1)[-1],
        "model_src_url": f"https://huggingface.co/{src_repo}",
        "conversion_mflux_ver": _mflux_version(),
        "conversion_date": (this_published or {}).get("upload_date") or date.today().isoformat(),
        "mflux_cli": config.get("model_cli_generate_command") or "mflux-generate",
        "model_steps": DEFAULT_MODEL_STEPS,
        # mflux's -q flag takes the quant bit-depth (3/4/5/6/8); bf16 is full
        # precision and takes no -q flag at all, so there's no integer to put
        # here -- left blank rather than a fabricated number.
        "model_quant_integer": quant[1:] if quant.lower().startswith("q") else "",
    }

    for q in QUANTS:
        repo_id = expected_repo_ids.get(q)
        entry = published_by_name.get(repo_id) if repo_id else None
        fields[f"{q}_gb"] = f"{entry['size_gb']:.2f}" if entry else "—"
        fields[f"{q}_url"] = f"https://huggingface.co/{repo_id}" if entry else "—"

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.format(**fields)


def render_sample_model_card() -> str:
    """Render the template for the first published (stem, quant) pair found
    -- there's no "selected model" concept on the GPU page yet, this is a
    preview of what the template produces against live data, not tied to any
    particular model."""
    from app.models_catalog import get_available_models, get_published_hf_manifest
    from app.models_hf import is_actually_published
    from app.models_missing import load_configs

    configs = load_configs()
    available = get_available_models(configs=configs)
    published_names = {
        m["model_name"]
        for m in get_published_hf_manifest().get("hf_models", [])
        if is_actually_published(m)
    }

    for stem, entry in available.items():
        if not entry["buildable"]:
            continue
        for quant, repo_id in entry["expected_repo_ids"].items():
            if repo_id in published_names:
                return render_model_card(stem, quant)

    raise ValueError("no published mflux-community model found to preview a model card from")
