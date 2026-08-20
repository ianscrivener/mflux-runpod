import pytest

from app.series_lifecycle import (
    delete_source_weights,
    series_is_complete,
    source_weights_path,
)


def test_source_weights_path(tmp_path):
    assert source_weights_path(tmp_path) == tmp_path / "source"


def test_delete_source_weights_removes_dir(tmp_path):
    source_dir = source_weights_path(tmp_path)
    source_dir.mkdir()
    (source_dir / "weights.safetensors").write_bytes(b"fake")

    delete_source_weights(tmp_path)

    assert not source_dir.exists()


def test_delete_source_weights_noop_when_absent(tmp_path):
    delete_source_weights(tmp_path)  # must not raise


def test_delete_source_weights_leaves_other_dirs_alone(tmp_path):
    source_dir = source_weights_path(tmp_path)
    source_dir.mkdir()
    build_dir = tmp_path / "fibo-mflux-q4"
    build_dir.mkdir()
    (build_dir / "weights.bin").write_bytes(b"built")

    delete_source_weights(tmp_path)

    assert not source_dir.exists()
    assert build_dir.exists()


@pytest.fixture
def sample_config():
    return {
        "collection": {"name": "Fibo"},
        "quants": ["q4", "q6"],
    }


def test_series_is_complete_true(sample_config):
    hf_manifest = {
        "hf_models": [
            {"model_name": "mflux-community/fibo-mflux-q4"},
            {"model_name": "mflux-community/fibo-mflux-q6"},
        ]
    }
    assert series_is_complete(sample_config, hf_manifest) is True


def test_series_is_complete_false_when_missing_one(sample_config):
    hf_manifest = {"hf_models": [{"model_name": "mflux-community/fibo-mflux-q4"}]}
    assert series_is_complete(sample_config, hf_manifest) is False


def test_series_is_complete_false_when_empty(sample_config):
    assert series_is_complete(sample_config, {"hf_models": []}) is False
