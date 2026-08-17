"""Missing models diff (PRD: /models_missing).

A model series is "supported" only once it has a configs/*.yaml file (that's what
defines its buildable quants list and Runner build metadata). Series present in
data/models_mflux.json but without a config are not yet buildable and are excluded
here, though they still show up under /models_supported.

A series is missing a quant if mflux-community/{slug}-mflux-{quant} isn't in the
current models_hf.json manifest. A series is "complete" only when every quant in
its config exists on HF.

Manual overrides (configs/overrides.yaml) can force-include a series into the
missing list regardless of HF state, or force-exclude it regardless of missing
quants.
"""

import re
from pathlib import Path

import yaml

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
OVERRIDES_PATH = CONFIGS_DIR / "overrides.yaml"
HF_ORG = "mflux-community"


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_configs(configs_dir: Path = CONFIGS_DIR) -> dict[str, dict]:
    """Return {config_stem: config_dict} for every configs/*.yaml file (excludes overrides.yaml)."""
    configs = {}
    for path in sorted(configs_dir.glob("*.yaml")):
        if path.name == "overrides.yaml":
            continue
        configs[path.stem] = yaml.safe_load(path.read_text())
    return configs


def load_overrides(overrides_path: Path = OVERRIDES_PATH) -> dict:
    """Return {"force_include": [...], "force_exclude": [...]} config stems."""
    if not overrides_path.exists():
        return {"force_include": [], "force_exclude": []}
    data = yaml.safe_load(overrides_path.read_text()) or {}
    return {
        "force_include": data.get("force_include") or [],
        "force_exclude": data.get("force_exclude") or [],
    }


def expected_repo_ids(config: dict) -> dict[str, str]:
    """Return {quant: repo_id} for every quant this config declares."""
    slug = slugify(config["collection"]["name"])
    return {quant: f"{HF_ORG}/{slug}-mflux-{quant}" for quant in config["quants"]}


def compute_missing(
    configs: dict[str, dict],
    hf_manifest: dict,
    overrides: dict | None = None,
) -> dict:
    """Diff configs' expected repos against the current HF manifest, then apply overrides.

    Returns {"missing": {config_stem: {...}}, "complete": [...]}. force_exclude
    removes a stem from "missing" (moved to "complete" instead, marked overridden).
    force_include adds a stem to "missing" with every quant listed, even if all
    already exist on HF.
    """
    overrides = overrides or {"force_include": [], "force_exclude": []}
    force_include = set(overrides.get("force_include") or [])
    force_exclude = set(overrides.get("force_exclude") or [])

    published = {m["model_name"] for m in hf_manifest.get("hf_models", [])}

    missing: dict[str, dict] = {}
    complete: list[str] = []

    for stem, config in configs.items():
        repo_ids = expected_repo_ids(config)
        missing_quants = [
            quant for quant, repo_id in repo_ids.items() if repo_id not in published
        ]

        if stem in force_exclude:
            complete.append(stem)
            continue

        if not missing_quants and stem in force_include:
            missing_quants = list(repo_ids.keys())

        if missing_quants:
            missing[stem] = {
                "hf_model_name": config.get("hf_model_name"),
                "missing_quants": missing_quants,
                "expected_repo_ids": {q: repo_ids[q] for q in missing_quants},
            }
        else:
            complete.append(stem)

    return {"missing": missing, "complete": complete}
