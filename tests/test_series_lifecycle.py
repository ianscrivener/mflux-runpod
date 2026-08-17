import pytest

from app.series_lifecycle import (
    delete_source_weights,
    series_is_complete,
    source_weights_path,
    teardown_if_complete,
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


def test_teardown_if_complete_skips_when_not_complete(sample_config, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.runpod_volumes.find_volume_for_series",
        lambda series: calls.append(series) or {"id": "should-not-be-used"},
    )

    deleted = teardown_if_complete("Fibo", sample_config, {"hf_models": []})

    assert deleted is False
    assert calls == []  # never even looked up the volume


def test_teardown_if_complete_deletes_when_complete(sample_config, monkeypatch):
    hf_manifest = {
        "hf_models": [
            {"model_name": "mflux-community/fibo-mflux-q4"},
            {"model_name": "mflux-community/fibo-mflux-q6"},
        ]
    }

    found_series = []
    deleted_ids = []
    monkeypatch.setattr(
        "app.runpod_volumes.find_volume_for_series",
        lambda series: found_series.append(series) or {"id": "vol-123"},
    )
    monkeypatch.setattr(
        "app.runpod_volumes.delete_volume",
        lambda volume_id: deleted_ids.append(volume_id),
    )

    deleted = teardown_if_complete("Fibo", sample_config, hf_manifest)

    assert deleted is True
    assert found_series == ["Fibo"]
    assert deleted_ids == ["vol-123"]


def test_teardown_if_complete_no_volume_found(sample_config, monkeypatch):
    hf_manifest = {
        "hf_models": [
            {"model_name": "mflux-community/fibo-mflux-q4"},
            {"model_name": "mflux-community/fibo-mflux-q6"},
        ]
    }
    monkeypatch.setattr("app.runpod_volumes.find_volume_for_series", lambda series: None)

    deleted = teardown_if_complete("Fibo", sample_config, hf_manifest)

    assert deleted is False
