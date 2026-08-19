import json

import pytest

from app.queue_store import QueueStoreConfigError, load_master, save_master


@pytest.fixture(autouse=True)
def do_spaces_env(monkeypatch):
    monkeypatch.setenv("DO_SPACES_KEY", "key")
    monkeypatch.setenv("DO_SPACES_SECRET", "secret")
    monkeypatch.setenv("DO_SPACES_ENDPOINT", "https://nyc3.digitaloceanspaces.com")
    monkeypatch.setenv("DO_SPACES_BUCKET", "mflux-runpod")


class _FakeClient:
    def __init__(self):
        self.puts = []
        self.store = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append((Bucket, Key, Body))
        self.store[Key] = Body

    def get_object(self, Bucket, Key):
        class _Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {"Body": _Body(self.store[Key])}


def test_save_master_uploads_local_file(monkeypatch, tmp_path):
    local_path = tmp_path / "models_queue.json"
    local_path.write_text(json.dumps({"queued": ["Fibo"]}))

    fake = _FakeClient()
    monkeypatch.setattr("boto3.client", lambda *a, **kw: fake)

    save_master(local_path)
    assert fake.puts[0][1] == "queue/models_queue.json"
    assert json.loads(fake.puts[0][2]) == {"queued": ["Fibo"]}


def test_load_master_overwrites_local_file(monkeypatch, tmp_path):
    local_path = tmp_path / "models_queue.json"
    local_path.write_text("{}")

    fake = _FakeClient()
    fake.store["queue/models_queue.json"] = json.dumps({"queued": ["Fibo"]}).encode()
    monkeypatch.setattr("boto3.client", lambda *a, **kw: fake)

    body = load_master(local_path)
    assert json.loads(body) == {"queued": ["Fibo"]}
    assert json.loads(local_path.read_text()) == {"queued": ["Fibo"]}


def test_missing_config_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("DO_SPACES_KEY", raising=False)
    with pytest.raises(QueueStoreConfigError, match="DO_SPACES_KEY"):
        save_master(tmp_path / "x.json")
