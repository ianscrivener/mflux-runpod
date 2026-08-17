import httpx
import pytest

from app.db import init_db
from app.runpod_volumes import (
    create_volume,
    delete_volume,
    find_volume_for_series,
    list_volumes,
    size_for_series,
    volume_name_for_series,
)


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "reports.sqlite"
    monkeypatch.setattr("app.db.DB_PATH", db_path)
    init_db(db_path)


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_volume_name_for_series_includes_series_and_is_unique():
    name1 = volume_name_for_series("Qwen-Image")
    name2 = volume_name_for_series("Qwen-Image")
    assert name1.startswith("mflux-")
    assert name1.endswith("-Qwen-Image")
    assert name1 != name2  # fresh uuid each call, not deterministic


def test_list_volumes(monkeypatch):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=[{"id": "v1", "name": "mflux-abc123-Fibo"}])

    result = list_volumes(_client_with(handler))
    assert result == [{"id": "v1", "name": "mflux-abc123-Fibo"}]


def test_find_volume_for_series_no_record():
    """No series_volumes row -- no RunPod API call happens at all, it's a DB lookup."""
    assert find_volume_for_series("Qwen-Image") is None


def test_create_volume_reuses_existing_from_db():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"id": "new-v", "name": "mflux-abc-Qwen-Image"})

    # First call creates and records it.
    first = create_volume("Qwen-Image", client=_client_with(handler))
    assert first["id"] == "new-v"
    assert calls == ["POST"]

    # Second call for the same series must reuse via the DB, not POST again.
    second = create_volume("Qwen-Image", client=_client_with(handler))
    assert second == {"id": "new-v", "name": "mflux-abc-Qwen-Image"}
    assert calls == ["POST"]  # no second POST


def test_create_volume_creates_new_when_absent():
    calls = []

    def handler(request):
        calls.append(request.method)
        assert request.method == "POST"
        import json

        body = json.loads(request.content)
        assert body["dataCenterId"] == "US-IL-1"
        assert body["size"] == 100
        assert body["name"].startswith("mflux-")
        assert body["name"].endswith("-Qwen-Image")
        return httpx.Response(200, json={"id": "new-v", "name": body["name"]})

    result = create_volume("Qwen-Image", client=_client_with(handler))
    assert result["id"] == "new-v"
    assert calls == ["POST"]


def test_size_for_series_adds_headroom(monkeypatch):
    monkeypatch.setattr("app.models_hf.hf_repo_size_gb", lambda repo_id: 63.0)
    assert size_for_series("Qwen/Qwen-Image-Edit-2511") == 93  # 63 + 30 headroom


def test_size_for_series_respects_minimum(monkeypatch):
    monkeypatch.setattr("app.models_hf.hf_repo_size_gb", lambda repo_id: 0.1)
    assert size_for_series("some/tiny-model") == 30  # 0.1 + 30, still above MIN


def test_delete_volume_marks_series_volumes_row_deleted():
    def handler(request):
        return httpx.Response(204)

    from app.runpod_volumes import _record_series_volume
    from app.db import get_connection

    _record_series_volume("Fibo", "v1", "mflux-abc-Fibo")
    delete_volume("v1", _client_with(handler))

    with get_connection() as conn:
        row = conn.execute(
            "SELECT deleted_at FROM series_volumes WHERE volume_id = ?", ("v1",)
        ).fetchone()
    assert row["deleted_at"] is not None
    assert find_volume_for_series("Fibo") is None  # no longer "active"


def test_get_volume(monkeypatch):
    from app.runpod_volumes import get_volume

    def handler(request):
        assert request.url.path == "/v1/networkvolumes/v1"
        return httpx.Response(200, json={"id": "v1", "name": "mflux-abc-Fibo"})

    assert get_volume("v1", _client_with(handler)) == {"id": "v1", "name": "mflux-abc-Fibo"}


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RUNPOD_API_KEY"):
        list_volumes()
