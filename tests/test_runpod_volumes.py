import httpx
import pytest

from app.runpod_volumes import (
    create_volume,
    delete_volume,
    find_volume_for_series,
    list_volumes,
    volume_name_for_series,
)


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")


def test_volume_name_for_series():
    assert volume_name_for_series("Qwen-Image") == "mflux-Qwen-Image"


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_volumes(monkeypatch):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=[{"id": "v1", "name": "mflux-Fibo"}])

    result = list_volumes(_client_with(handler))
    assert result == [{"id": "v1", "name": "mflux-Fibo"}]


def test_find_volume_for_series_match():
    def handler(request):
        return httpx.Response(
            200,
            json=[
                {"id": "v1", "name": "mflux-Fibo"},
                {"id": "v2", "name": "mflux-Qwen-Image"},
            ],
        )

    found = find_volume_for_series("Qwen-Image", _client_with(handler))
    assert found == {"id": "v2", "name": "mflux-Qwen-Image"}


def test_find_volume_for_series_no_match():
    def handler(request):
        return httpx.Response(200, json=[{"id": "v1", "name": "mflux-Fibo"}])

    assert find_volume_for_series("Qwen-Image", _client_with(handler)) is None


def test_create_volume_reuses_existing():
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.method == "GET"
        return httpx.Response(
            200, json=[{"id": "v1", "name": "mflux-Qwen-Image"}]
        )

    result = create_volume("Qwen-Image", client=_client_with(handler))
    assert result == {"id": "v1", "name": "mflux-Qwen-Image"}
    assert calls == ["GET"]  # no POST — reused existing volume


def test_create_volume_creates_new_when_absent():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        assert request.method == "POST"
        import json

        body = json.loads(request.content)
        assert body == {
            "name": "mflux-Qwen-Image",
            "dataCenterId": "US-CA-2",
            "size": 100,
        }
        return httpx.Response(200, json={"id": "new-v", "name": "mflux-Qwen-Image"})

    result = create_volume("Qwen-Image", client=_client_with(handler))
    assert result == {"id": "new-v", "name": "mflux-Qwen-Image"}
    assert calls == ["GET", "POST"]


def test_delete_volume():
    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path == "/v1/networkvolumes/v1"
        return httpx.Response(204)

    delete_volume("v1", _client_with(handler))  # must not raise


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RUNPOD_API_KEY"):
        list_volumes()
