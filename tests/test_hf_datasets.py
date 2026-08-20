import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.hf_datasets import HfDatasetConfigError, list_datasets, load_dataset_config, pull, push


@pytest.fixture(autouse=True)
def hf_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def state_and_data_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("app.hf_datasets.SYNC_STATE_PATH", tmp_path / ".hf_sync_state.json")
    return tmp_path


def _bucket_file(path, xet_hash, size=100):
    return SimpleNamespace(path=path, xet_hash=xet_hash, size=size, mtime=None, uploaded_at=None)


def test_load_dataset_config_has_nine_datasets():
    config = load_dataset_config()
    assert config["bucket_id"] == "mflux-community/ci"
    assert set(config["datasets"]) == {
        "models_mflux",
        "models_hf",
        "models_missing",
        "models_src_details",
        "runpod_gpu_skus",
        "logs_devops",
        "logs_conversions",
        "models_queue",
        "models_skipped",
    }
    assert config["datasets"]["models_mflux"]["writable"] is False


def test_unknown_dataset_raises():
    with pytest.raises(HfDatasetConfigError, match="no such dataset"):
        pull("not_a_real_dataset")


def test_missing_hf_token_raises(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(HfDatasetConfigError, match="HF_TOKEN"):
        pull("models_hf")


def test_pull_downloads_on_change(monkeypatch, tmp_path):
    local_path = tmp_path / "models_hf.json"
    monkeypatch.setattr(
        "app.hf_datasets.load_dataset_config",
        lambda: {
            "bucket_id": "mflux-community/ci",
            "datasets": {"models_hf": {"path_in_repo": "models_hf.json", "local_path": str(local_path), "writable": True}},
        },
    )

    downloaded = []

    def fake_get_bucket_paths_info(bucket_id, paths, token=None):
        return [_bucket_file("models_hf.json", "hash-v2")]

    def fake_download_bucket_files(bucket_id, files, token=None, raise_on_missing_files=False):
        downloaded.append(files)
        for _remote_path, dest in files:
            Path(dest).write_text("{}")

    monkeypatch.setattr("huggingface_hub.get_bucket_paths_info", fake_get_bucket_paths_info)
    monkeypatch.setattr("huggingface_hub.download_bucket_files", fake_download_bucket_files)

    result = pull("models_hf")
    assert result == {"changed": True, "xet_hash": "hash-v2", "size": 100}
    assert downloaded == [[("models_hf.json", local_path)]]

    # Second pull with the same hash should NOT download again.
    downloaded.clear()
    result2 = pull("models_hf")
    assert result2["changed"] is False
    assert downloaded == []


def test_push_refuses_non_writable(monkeypatch):
    monkeypatch.setattr(
        "app.hf_datasets.load_dataset_config",
        lambda: {
            "bucket_id": "mflux-community/ci",
            "datasets": {"models_mflux": {"path_in_repo": "models_mflux.json", "local_path": "x.json", "writable": False}},
        },
    )
    with pytest.raises(HfDatasetConfigError, match="not writable"):
        push("models_mflux")


def test_push_uploads_and_updates_state(monkeypatch, tmp_path):
    local_path = tmp_path / "models_queue.json"
    local_path.write_text(json.dumps({"queued": []}))

    monkeypatch.setattr(
        "app.hf_datasets.load_dataset_config",
        lambda: {
            "bucket_id": "mflux-community/ci",
            "datasets": {"models_queue": {"path_in_repo": "models_queue.json", "local_path": str(local_path), "writable": True}},
        },
    )

    uploaded = []

    def fake_batch_bucket_files(bucket_id, add=None, copy=None, delete=None, token=None):
        uploaded.append(add)

    def fake_get_bucket_paths_info(bucket_id, paths, token=None):
        return [_bucket_file("models_queue.json", "hash-after-push")]

    monkeypatch.setattr("huggingface_hub.batch_bucket_files", fake_batch_bucket_files)
    monkeypatch.setattr("huggingface_hub.get_bucket_paths_info", fake_get_bucket_paths_info)

    result = push("models_queue")
    assert result == {"pushed": True, "xet_hash": "hash-after-push", "size": 100}
    assert uploaded == [[(local_path, "models_queue.json")]]


def test_list_datasets_reports_local_existence(tmp_path, monkeypatch):
    local_path = tmp_path / "models_hf.json"
    local_path.write_text("{}")
    monkeypatch.setattr(
        "app.hf_datasets.load_dataset_config",
        lambda: {
            "bucket_id": "mflux-community/ci",
            "datasets": {"models_hf": {"path_in_repo": "models_hf.json", "local_path": str(local_path), "writable": True}},
        },
    )
    result = list_datasets()
    assert len(result) == 1
    assert result[0]["name"] == "models_hf"
    assert result[0]["local_exists"] is True
    assert result[0]["local_mtime"] is not None
