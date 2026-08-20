import json

import pytest

from app.queue_store import QueueStoreConfigError, load_master, save_master


@pytest.fixture(autouse=True)
def hf_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "test-token")


def test_save_master_uploads_local_file(monkeypatch, tmp_path):
    local_path = tmp_path / "models_queue.json"
    local_path.write_text(json.dumps({"queued": ["Fibo"]}))

    calls = []
    monkeypatch.setattr(
        "huggingface_hub.batch_bucket_files",
        lambda bucket_id, add=None, delete=None, token=None: calls.append((bucket_id, add, token)),
    )

    save_master(local_path)

    assert len(calls) == 1
    bucket_id, add, token = calls[0]
    assert bucket_id == "cleverheart2026/mflux-model-gpu-runner-storage"
    assert add == [(json.dumps({"queued": ["Fibo"]}).encode(), "queue/models_queue.json")]
    assert token == "test-token"


def test_load_master_overwrites_local_file(monkeypatch, tmp_path):
    local_path = tmp_path / "models_queue.json"
    local_path.write_text("{}")

    def fake_download(bucket_id, files, token=None):
        (remote_key, dest_path) = files[0]
        dest_path.write_text(json.dumps({"queued": ["Fibo"]}))

    monkeypatch.setattr("huggingface_hub.download_bucket_files", fake_download)

    body = load_master(local_path)
    assert json.loads(body) == {"queued": ["Fibo"]}
    assert json.loads(local_path.read_text()) == {"queued": ["Fibo"]}


def test_missing_config_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(QueueStoreConfigError, match="HF_TOKEN"):
        save_master(tmp_path / "x.json")
