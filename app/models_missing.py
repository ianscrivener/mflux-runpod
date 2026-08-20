"""Missing models diff (PRD: /models_missing).

A model series is "supported" only once it has a configs/models/*.yaml file
(that's what defines its buildable quants list and Runner build metadata).
Series present in data-hf-sync/models_mflux.json but without a config are not
yet buildable and are excluded here, though they still show up under
/models_mflux.

A series is missing a quant if mflux-community/{slug}-mflux-{quant} isn't in the
current models_hf.json manifest. A series is "complete" only when every quant in
its config exists on HF.

Manual overrides (configs/overrides.yaml) can force-include a series into the
missing list regardless of HF state, or force-exclude it regardless of missing
quants.
"""

import json
import re
import tempfile
from pathlib import Path

import yaml

MODELS_MISSING_PATH = Path(__file__).resolve().parent.parent / "data-hf-sync" / "models_missing.json"
MODELS_SKIPPED_PATH = Path(__file__).resolve().parent.parent / "data-hf-sync" / "models_skipped.json"

CONFIGS_ROOT = Path(__file__).resolve().parent.parent / "configs"
CONFIGS_DIR = CONFIGS_ROOT / "models"
OVERRIDES_PATH = CONFIGS_ROOT / "overrides.yaml"
HF_ORG = "mflux-community"

# The hardcoded default quant set every models_mflux.json catalog entry is
# assumed to support, replacing configs/models/*.yaml's per-model `quants`
# field as the source of truth for app.models_catalog.get_available_models().
# compute_missing() below is UNCHANGED and still reads each config's own
# `quants` list -- it backs real dispatch (generate_one), which still
# requires a configs/models/*.yaml file to exist regardless of this set.
DEFAULT_QUANTS = ["q3", "q4", "q5", "q6", "q8", "bf16"]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_configs(configs_dir: Path = CONFIGS_DIR) -> dict[str, dict]:
    """Return {config_stem: config_dict} for every configs/models/*.yaml file.
    Nothing to exclude here anymore -- overrides.yaml and hf_datasets.yaml
    live as siblings of models/, not inside it, specifically so this glob
    can never again pick up a non-model config by accident (confirmed live
    2026-08-19: hf_datasets.yaml briefly living directly under configs/
    broke this exact function)."""
    return {
        path.stem: yaml.safe_load(path.read_text())
        for path in sorted(configs_dir.glob("*.yaml"))
    }


def load_overrides(overrides_path: Path = OVERRIDES_PATH) -> dict:
    """Return {"force_include": [...], "force_exclude": [...]} config stems."""
    if not overrides_path.exists():
        return {"force_include": [], "force_exclude": []}
    data = yaml.safe_load(overrides_path.read_text()) or {}
    return {
        "force_include": data.get("force_include") or [],
        "force_exclude": data.get("force_exclude") or [],
    }


def load_models_skipped(path: Path = MODELS_SKIPPED_PATH) -> dict:
    """Parse data-hf-sync/models_skipped.json -- four ways to exclude a
    models_mflux.json catalog entry (or one of its quants) from
    app.models_catalog.get_available_models()'s computed set:

        {
          "skipped_familys": ["Flux1"],             # whole model_family, case-insensitive
          "skipped_model_sub_familys": ["flux1-depth"],  # whole model_sub_family, case-insensitive
          "skipped_models": ["dev-kontext"],         # one catalog slug OR config stem entirely
          "skipped_quants": {"flux2-klein-9b-kv": ["q3"]}  # specific quants of one slug
        }

    skipped_models entries may name either a models_mflux.json catalog slug
    (the normal case) or a configs/models/*.yaml stem -- the latter is the
    only way to skip a config that has no catalog match at all (e.g.
    "Qwen-Image-Layered", which isn't in models_mflux.json yet, so it has no
    slug to skip by).

    Returns {"families": set, "sub_families": set, "models": set, "quants": dict}
    (families/sub_families/models all lowercased for comparison -- catalog
    slugs are already lowercase by convention, but config stems aren't
    (e.g. "Qwen-Image-Layered"), and this file is hand-edited, so casing
    drifts either way; app.models_catalog.compute_available_models lowercases
    the slug/stem it checks against this set to match).
    Hand-edited directly on disk by design -- deliberately NOT registered in
    configs/hf_datasets.yaml, so app.main's hf_sync loop never pulls over an
    in-progress local edit before it's ever pushed anywhere. Missing file
    (nothing skipped yet) degrades to all-empty."""
    empty = {"families": set(), "sub_families": set(), "models": set(), "quants": {}}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text()) or {}
    except json.JSONDecodeError:
        # Hand-edited file, mid-edit or just broken -- degrade to "nothing
        # skipped" rather than crash every /models_available call over it
        # (confirmed live this session: a stray trailing comma did exactly
        # that before this guard existed).
        return empty

    def _string_set(key: str) -> set[str]:
        values = data.get(key)
        if not isinstance(values, list):
            # Missing, null, or some other hand-editing mistake (a dict/
            # string where a list was expected) -- treat as empty rather
            # than crash on the isinstance check below.
            return set()
        return {v.lower() for v in values if isinstance(v, str)}

    quants = data.get("skipped_quants", {})
    if not isinstance(quants, dict):
        # Hand-editing mistake: someone wrote "skipped_quants": [] (an empty
        # list reads naturally as "nothing here") instead of {} -- coerce
        # rather than crash every /models_available call over it.
        quants = {}
    return {
        "families": _string_set("skipped_familys"),
        "sub_families": _string_set("skipped_model_sub_familys"),
        "models": _string_set("skipped_models"),
        "quants": quants,
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


def refresh_models_missing() -> dict:
    """Materialize the current compute_missing() result to data-hf-sync/models_missing.json
    and publish it to the HF bucket (app.hf_datasets) -- a snapshot for anyone
    consuming the bucket directly, not the live read path. GET /models_missing
    stays fully live-computed; this is purely a publish-on-demand action."""
    from app.hf_datasets import push
    from app.models_hf import load_models_hf

    result = compute_missing(load_configs(), load_models_hf(), load_overrides())

    MODELS_MISSING_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=MODELS_MISSING_PATH.parent, delete=False
    ) as tmp:
        json.dump(result, tmp, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(MODELS_MISSING_PATH)

    push("models_missing")

    return result
