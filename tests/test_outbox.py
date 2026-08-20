import json

import pytest

from app.outbox import (
    OutboxConfigError,
    delete_result,
    get_result,
    list_pending,
    process_pending,
    put_result,
)


@pytest.fixture(autouse=True)
def hf_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "test-token")


def test_put_result_requires_hf_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(OutboxConfigError, match="HF_TOKEN"):
        put_result(1, "q4", {"x": 1})


def test_put_result_calls_batch_bucket_files(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "huggingface_hub.batch_bucket_files",
        lambda bucket_id, add=None, delete=None, token=None: calls.append(
            (bucket_id, add, delete, token)
        ),
    )

    key = put_result(7, "q6", {"status": "uploaded"})

    assert key == "results/7/q6.json"
    assert len(calls) == 1
    bucket_id, add, delete, token = calls[0]
    assert bucket_id == "cleverheart2026/mflux-model-gpu-runner-storage"
    assert add == [(json.dumps({"status": "uploaded"}).encode("utf-8"), "results/7/q6.json")]
    assert delete is None
    assert token == "test-token"


def test_put_result_respects_bucket_override(monkeypatch):
    monkeypatch.setenv("OUTBOX_BUCKET_ID", "someone/other-bucket")
    calls = []
    monkeypatch.setattr(
        "huggingface_hub.batch_bucket_files",
        lambda bucket_id, add=None, delete=None, token=None: calls.append(bucket_id),
    )

    put_result(1, "q4", {})

    assert calls == ["someone/other-bucket"]


def test_list_pending_returns_paths(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "huggingface_hub.list_bucket_tree",
        lambda bucket_id, prefix=None, recursive=None, token=None: [
            SimpleNamespace(path="results/1/q4.json"),
            SimpleNamespace(path="results/2/q8.json"),
        ],
    )

    assert list_pending() == ["results/1/q4.json", "results/2/q8.json"]


def test_get_result_downloads_and_parses(monkeypatch, tmp_path):
    payload = {"finished_at": "2026-08-20T00:00:00Z", "quant_builds": []}

    def fake_download(bucket_id, files, token=None):
        (remote_key, local_path) = files[0]
        local_path.write_text(json.dumps(payload))

    monkeypatch.setattr("huggingface_hub.download_bucket_files", fake_download)

    assert get_result("results/1/q4.json") == payload


def test_delete_result_calls_batch_bucket_files_delete(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "huggingface_hub.batch_bucket_files",
        lambda bucket_id, add=None, delete=None, token=None: calls.append((bucket_id, delete)),
    )

    delete_result("results/1/q4.json")

    assert calls == [("cleverheart2026/mflux-model-gpu-runner-storage", ["results/1/q4.json"])]


def test_process_pending_applies_and_deletes(monkeypatch):
    from types import SimpleNamespace

    payload = {
        "finished_at": "2026-08-20T00:00:00Z",
        "error": None,
        "quant_builds": [{"quant": "q4", "status": "uploaded", "hf_repo_id": "mflux-community/x-mflux-q4"}],
    }
    deleted = []
    applied = []

    monkeypatch.setattr(
        "huggingface_hub.list_bucket_tree",
        lambda bucket_id, prefix=None, recursive=None, token=None: [
            SimpleNamespace(path="results/9/q4.json")
        ],
    )

    def fake_download(bucket_id, files, token=None):
        (remote_key, local_path) = files[0]
        local_path.write_text(json.dumps(payload))

    monkeypatch.setattr("huggingface_hub.download_bucket_files", fake_download)
    monkeypatch.setattr(
        "huggingface_hub.batch_bucket_files",
        lambda bucket_id, add=None, delete=None, token=None: deleted.append(delete),
    )
    monkeypatch.setattr("app.report.add_quant_build", lambda *a, **k: applied.append((a, k)))
    monkeypatch.setattr("app.report.update_run_status_from_children", lambda *a, **k: None)

    result = process_pending()

    assert result["processed"] == ["results/9/q4.json"]
    assert result["errors"] == []
    assert deleted == [["results/9/q4.json"]]
    assert len(applied) == 1
