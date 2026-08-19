from pathlib import Path

import pytest
import yaml

from app.models_missing import (
    compute_missing,
    expected_repo_ids,
    load_configs,
    load_overrides,
    slugify,
)


def test_slugify():
    assert slugify("Qwen-Image Edit") == "qwen-image-edit"
    assert slugify("Flux.1 Dev") == "flux-1-dev"


def test_load_configs_reads_real_configs_dir():
    configs = load_configs()
    assert "Qwen-Image-Edit" in configs
    assert configs["Qwen-Image-Edit"]["model_object"] == "QwenImageEdit"


def test_expected_repo_ids():
    config = {"collection": {"name": "Fibo"}, "quants": ["q4", "q6"]}
    assert expected_repo_ids(config) == {
        "q4": "mflux-community/fibo-mflux-q4",
        "q6": "mflux-community/fibo-mflux-q6",
    }


@pytest.fixture
def sample_configs(tmp_path):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    (configs_dir / "Fibo.yaml").write_text(
        yaml.safe_dump(
            {
                "model_object": "Fibo",
                "model_config": "fibo",
                "quants": ["q4", "q6", "q8"],
                "collection": {"name": "Fibo", "description": "x", "version": "1.0.0"},
            }
        )
    )
    (configs_dir / "Complete.yaml").write_text(
        yaml.safe_dump(
            {
                "model_object": "Complete",
                "model_config": "complete",
                "quants": ["bf16"],
                "collection": {"name": "Complete", "description": "x", "version": "1.0.0"},
            }
        )
    )
    return load_configs(configs_dir)


def test_compute_missing_identifies_missing_quants(sample_configs):
    hf_manifest = {
        "hf_models": [
            {"model_name": "mflux-community/fibo-mflux-q4"},
            {"model_name": "mflux-community/complete-mflux-bf16"},
        ]
    }

    result = compute_missing(sample_configs, hf_manifest)

    assert result["missing"]["Fibo"]["missing_quants"] == ["q6", "q8"]
    assert "Complete" not in result["missing"]
    assert result["complete"] == ["Complete"]


def test_compute_missing_all_missing_when_hf_empty(sample_configs):
    result = compute_missing(sample_configs, {"hf_models": []})

    assert set(result["missing"]["Fibo"]["missing_quants"]) == {"q4", "q6", "q8"}
    assert result["missing"]["Complete"]["missing_quants"] == ["bf16"]
    assert result["complete"] == []


def test_configs_dir_and_overrides_path_are_siblings_not_nested():
    """overrides.yaml (and hf_datasets.yaml/runpod.yaml) live as siblings of
    configs/models/, not inside it -- that's what stops load_configs()'s glob
    from ever picking up a non-model config by accident (it did, once, when
    hf_datasets.yaml briefly lived directly under configs/ -- see
    app.models_missing's module docstring)."""
    from app.models_missing import CONFIGS_DIR, CONFIGS_ROOT, OVERRIDES_PATH

    assert CONFIGS_DIR == CONFIGS_ROOT / "models"
    assert OVERRIDES_PATH == CONFIGS_ROOT / "overrides.yaml"
    assert OVERRIDES_PATH.parent == CONFIGS_DIR.parent


def test_load_configs_reads_every_yaml_in_the_given_dir(tmp_path):
    """No special-casing needed anymore -- load_configs() just loads
    whatever *.yaml is in the directory it's pointed at. In production
    that's always configs/models/, which never contains overrides.yaml."""
    configs_dir = tmp_path / "models"
    configs_dir.mkdir()
    (configs_dir / "Fibo.yaml").write_text(
        yaml.safe_dump(
            {
                "model_object": "Fibo",
                "model_config": "fibo",
                "quants": ["q4"],
                "collection": {"name": "Fibo"},
            }
        )
    )

    configs = load_configs(configs_dir)
    assert list(configs.keys()) == ["Fibo"]


def test_load_overrides_missing_file_returns_empty(tmp_path):
    assert load_overrides(tmp_path / "nope.yaml") == {
        "force_include": [],
        "force_exclude": [],
    }


def test_load_overrides_reads_real_file():
    overrides = load_overrides()
    assert overrides == {"force_include": [], "force_exclude": []}


def test_compute_missing_force_exclude_moves_to_complete(sample_configs):
    hf_manifest = {"hf_models": []}
    overrides = {"force_include": [], "force_exclude": ["Fibo"]}

    result = compute_missing(sample_configs, hf_manifest, overrides)

    assert "Fibo" not in result["missing"]
    assert "Fibo" in result["complete"]
    # Complete still genuinely missing bf16, unaffected by unrelated override
    assert result["missing"]["Complete"]["missing_quants"] == ["bf16"]


def test_compute_missing_force_include_adds_complete_series(sample_configs):
    hf_manifest = {
        "hf_models": [{"model_name": "mflux-community/complete-mflux-bf16"}]
    }
    overrides = {"force_include": ["Complete"], "force_exclude": []}

    result = compute_missing(sample_configs, hf_manifest, overrides)

    assert result["missing"]["Complete"]["missing_quants"] == ["bf16"]
    assert "Complete" not in result["complete"]


def test_compute_missing_force_exclude_wins_over_force_include(sample_configs):
    hf_manifest = {"hf_models": []}
    overrides = {"force_include": ["Fibo"], "force_exclude": ["Fibo"]}

    result = compute_missing(sample_configs, hf_manifest, overrides)

    assert "Fibo" not in result["missing"]
    assert "Fibo" in result["complete"]


def test_every_real_config_declares_hf_model_name():
    """Regression guard: task 6 needs hf_model_name to download source weights.
    Every configs/*.yaml must at least declare the key (Qwen-Image-Layered is the
    one known exception, deliberately left null pending manual research)."""
    configs = load_configs()
    missing_key = [stem for stem, c in configs.items() if "hf_model_name" not in c]
    assert missing_key == [], f"configs missing hf_model_name key: {missing_key}"

    null_valued = [stem for stem, c in configs.items() if c["hf_model_name"] is None]
    assert null_valued == ["Qwen-Image-Layered"], (
        f"expected only Qwen-Image-Layered to have a null hf_model_name, got: {null_valued}"
    )
