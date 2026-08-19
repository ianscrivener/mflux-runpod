import pytest

from app.queue import (
    QueueValidationError,
    add_entry,
    delete_entry,
    list_entries,
    update_entry,
)


@pytest.fixture(autouse=True)
def isolated_queue_file(tmp_path, monkeypatch):
    local_path = tmp_path / "models_queue.json"
    monkeypatch.setattr("app.queue.LOCAL_PATH", local_path)
    monkeypatch.setattr("app.queue_store.publish", lambda: {"master": "do_spaces", "mirror": {}})


def test_list_entries_empty_when_no_file():
    assert list_entries() == []


def test_add_entry_unknown_model_raises():
    with pytest.raises(QueueValidationError, match="not a known model"):
        add_entry("Not-A-Real-Model")


def test_add_entry_assigns_incrementing_ids():
    first = add_entry("Fibo")
    second = add_entry("Qwen-Image")
    assert first["id"] == 1
    assert second["id"] == 2
    assert first["status"] == "pending"
    assert [e["id"] for e in list_entries()] == [1, 2]


def test_add_entry_defaults_quants_to_none():
    entry = add_entry("Fibo")
    assert entry["quants"] is None
    assert entry["force_hf_overwrite"] is False


def test_update_entry_changes_status():
    entry = add_entry("Fibo")
    updated = update_entry(entry["id"], status="approved")
    assert updated["status"] == "approved"
    assert list_entries()[0]["status"] == "approved"


def test_update_entry_rejects_unknown_status():
    entry = add_entry("Fibo")
    with pytest.raises(QueueValidationError, match="status must be one of"):
        update_entry(entry["id"], status="bogus")


def test_update_entry_unknown_id_raises():
    with pytest.raises(QueueValidationError, match="no queue entry"):
        update_entry(999, status="approved")


def test_update_entry_partial_update_preserves_other_fields():
    entry = add_entry("Fibo", quants=["q4"], note="test note")
    updated = update_entry(entry["id"], status="approved")
    assert updated["quants"] == ["q4"]
    assert updated["note"] == "test note"


def test_delete_entry_removes_it():
    entry = add_entry("Fibo")
    delete_entry(entry["id"])
    assert list_entries() == []


def test_delete_entry_unknown_id_raises():
    with pytest.raises(QueueValidationError, match="no queue entry"):
        delete_entry(999)


def test_load_handles_placeholder_stub(tmp_path, monkeypatch):
    local_path = tmp_path / "models_queue.json"
    local_path.write_text('{"place": "holder"}')
    monkeypatch.setattr("app.queue.LOCAL_PATH", local_path)
    assert list_entries() == []


def test_publish_failure_does_not_fail_the_local_mutation(monkeypatch):
    """Confirmed live 2026-08-19: an unconfigured DO_SPACES_* turned every
    add/update/delete into a raw 500 despite the local write succeeding.
    The local mutation must always succeed; publish failure is reported,
    not raised."""
    from app.hf_datasets import HfDatasetConfigError

    def failing_publish():
        raise HfDatasetConfigError("HF_TOKEN not set")

    monkeypatch.setattr("app.queue_store.publish", failing_publish)

    entry = add_entry("Fibo")
    assert entry["published"] is False
    assert "HF_TOKEN" in entry["publish_error"]
    assert list_entries()[0]["model_stem"] == "Fibo"  # local write still happened

    updated = update_entry(entry["id"], status="approved")
    assert updated["published"] is False
    assert list_entries()[0]["status"] == "approved"

    result = delete_entry(entry["id"])
    assert result["published"] is False
    assert list_entries() == []
