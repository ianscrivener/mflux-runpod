import httpx
import pytest

from app.runpod_skus import RunpodSkusConfigError, fetch_gpu_skus, refresh_gpu_skus, write_gpu_skus


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_gpu_skus_returns_list(monkeypatch):
    def handler(request):
        assert request.url.path == "/v2/catalog/gpus"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"gpus": [{"id": "NVIDIA GeForce RTX 4090", "price": {"secure": 0.74}}]})

    result = fetch_gpu_skus(_client_with(handler))
    assert result == [{"id": "NVIDIA GeForce RTX 4090", "price": {"secure": 0.74}}]


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    with pytest.raises(RunpodSkusConfigError, match="RUNPOD_API_KEY"):
        fetch_gpu_skus()


def test_write_gpu_skus_atomic(tmp_path):
    data_path = tmp_path / "runpod_gpu_skus.json"
    write_gpu_skus([{"id": "x"}], data_path)
    assert data_path.exists()
    import json

    assert json.loads(data_path.read_text()) == [{"id": "x"}]


def test_refresh_gpu_skus_writes_and_pushes(monkeypatch, tmp_path):
    data_path = tmp_path / "runpod_gpu_skus.json"
    pushed = []

    def handler(request):
        return httpx.Response(200, json={"gpus": [{"id": "NVIDIA GeForce RTX 4090"}]})

    monkeypatch.setattr("app.hf_datasets.push", lambda name: pushed.append(name))

    result = refresh_gpu_skus(_client_with(handler), data_path)
    assert result == [{"id": "NVIDIA GeForce RTX 4090"}]
    assert data_path.exists()
    assert pushed == ["runpod_gpu_skus"]
