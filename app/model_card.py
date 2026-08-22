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

# mflux CLI quirks not expressible from model_cli_generate_command alone --
# confirmed live 2026-08-22 against the installed mflux CLI's own argparse
# defaults/parser definitions (`mflux-generate-* --help`, and reading each
# CLI module's build_parser()/main() source). Keyed by configs/models/*.yaml
# stem; only the models where the CLI's own default silently diverges from
# what the config actually builds, or where a flag is outright required,
# need an entry here.
#
# - mflux-generate-fibo defaults --model to "fibo", so Fibo-lite's example
#   command silently built plain FIBO instead of FIBO-lite without an
#   explicit --model flag. Likewise mflux-generate-fibo-edit defaults
#   --model to "fibo-edit", so Fibo-Edit-RMBG needs it spelled out too.
# - FIBO Edit's get_json_prompt_for_edit() raises ValueError outright when
#   given a plain (non-JSON) prompt with no --image-path -- exactly what the
#   sample prompt below is -- so every FIBO Edit variant's example would
#   just crash without one.
# - mflux-generate-controlnet's build_parser() calls add_model_arguments
#   (require_model_arg=True) -- --model is a hard argparse requirement here,
#   not just a default; omitting it fails before generation even starts.
#   Both ControlNet-Canny configs share this one CLI script (base model
#   picked via --model), so both need it spelled out. --controlnet-image-path
#   isn't argparse-required, but a controlnet run without one just ignores
#   the whole point of the tool, so it's included for a useful example too.
# - mflux-generate-depth's --depth-image-path isn't required either (defaults
#   to None, no crash), included for the same reason.
_CLI_QUIRKS = {
    "Fibo-lite": {"extra_args": "--model fibo-lite"},
    "Fibo-Edit": {"image_flag": "--image-path"},
    "Fibo-Edit-RMBG": {"extra_args": "--model fibo-edit-rmbg", "image_flag": "--image-path"},
    "Flux.1-Dev-ControlNet-Canny": {"extra_args": "--model dev-controlnet-canny", "image_flag": "--controlnet-image-path"},
    "Flux.1-Schnell-ControlNet-Canny": {"extra_args": "--model schnell-controlnet-canny", "image_flag": "--controlnet-image-path"},
    "Flux.1-Dev-Depth": {"image_flag": "--depth-image-path"},
}


def _build_command(stem: str, cli: str, quant: str, steps: int) -> str:
    """Full multi-line `mflux-generate-... \\` usage example for one
    (stem, quant) pair -- computed here rather than left to the template's
    plain str.format() so bf16's -q flag can be omitted entirely (mflux's
    -q takes a quant bit-depth; bf16 is full precision and has none) instead
    of rendering as a bare, valueless `-q ` (previously str.format()'s only
    option, since it has no conditional syntax)."""
    quirks = _CLI_QUIRKS.get(stem, {})
    lines = [cli]
    if quirks.get("extra_args"):
        lines.append(quirks["extra_args"])
    if quirks.get("image_flag"):
        lines.append(f"{quirks['image_flag']} /path/to/your/image.png")
    lines += [
        '--prompt "A puffin standing on a cliff"',
        "--width 1280",
        "--height 500",
        "--seed 42",
        f"--steps {steps}",
    ]
    if quant.lower().startswith("q"):
        lines.append(f"-q {quant[1:]}")
    return " \\\n  ".join(lines)


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

    cli = config.get("model_cli_generate_command") or "mflux-generate"

    fields = {
        "model_name": (config.get("collection") or {}).get("name", stem),
        "model_quant": quant.upper(),
        "model_src": src_repo.rsplit("/", 1)[-1],
        "model_src_url": f"https://huggingface.co/{src_repo}",
        "conversion_mflux_ver": _mflux_version(),
        "conversion_date": (this_published or {}).get("upload_date") or date.today().isoformat(),
        "command": _build_command(stem, cli, quant, DEFAULT_MODEL_STEPS),
    }

    for q in QUANTS:
        repo_id = expected_repo_ids.get(q)
        entry = published_by_name.get(repo_id) if repo_id else None
        size_gb = entry.get("size_gb") if entry else None
        fields[f"{q}_gb"] = f"{size_gb:.2f}" if size_gb is not None else "—"
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
