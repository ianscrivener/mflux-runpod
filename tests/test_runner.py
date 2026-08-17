import json
from pathlib import Path

import pytest

from app.runner import (
    build_and_upload_one_quant,
    ensure_collection,
    hash_dir,
    is_locally_valid,
)
import app.runner as runner_module


def test_hash_dir_ignores_manifest_json(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"hello")
    (tmp_path / "manifest.json").write_text('{"sha256": "whatever"}')
    h1 = hash_dir(tmp_path)

    (tmp_path / "manifest.json").write_text('{"sha256": "different"}')
    h2 = hash_dir(tmp_path)

    assert h1 == h2


def test_hash_dir_changes_with_content(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"hello")
    h1 = hash_dir(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"world")
    h2 = hash_dir(tmp_path)
    assert h1 != h2


def test_is_locally_valid_false_when_missing(tmp_path):
    assert is_locally_valid(tmp_path / "nope") is False


def test_is_locally_valid_false_without_manifest(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "a.bin").write_bytes(b"x")
    assert is_locally_valid(build) is False


def test_is_locally_valid_true_when_hash_matches(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "a.bin").write_bytes(b"x")
    (build / "manifest.json").write_text(json.dumps({"sha256": hash_dir(build)}))
    assert is_locally_valid(build) is True


def test_is_locally_valid_false_when_hash_stale(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "a.bin").write_bytes(b"x")
    (build / "manifest.json").write_text(json.dumps({"sha256": "stale-hash"}))
    assert is_locally_valid(build) is False


def test_is_locally_valid_false_when_manifest_malformed(tmp_path):
    """A truncated/corrupt manifest.json (e.g. from a crash mid-write) must
    be treated as an invalid build, not raise, so the caller rebuilds."""
    build = tmp_path / "build"
    build.mkdir()
    (build / "a.bin").write_bytes(b"x")
    (build / "manifest.json").write_text("{not valid json")
    assert is_locally_valid(build) is False


# ---- build_and_upload_one_quant, with mflux/HfApi faked out ----
# One GPU job = one quant (decided with the user: crash isolation + retry
# granularity per quant, not per series).


class FakeModel:
    def __init__(self, quantize, model_config):
        self.quantize = quantize
        self.model_config = model_config

    def save_model(self, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "weights.bin").write_bytes(b"fake-weights")


class FakeCreated:
    slug = "fake-collection-slug"


class FakeApi:
    def __init__(self, existing_repos=None):
        self.existing_repos = set(existing_repos or [])
        self.uploaded = []
        self.deleted = []
        self.collection_items = []

    def repo_exists(self, repo_id):
        return repo_id in self.existing_repos

    def delete_repo(self, repo_id):
        self.deleted.append(repo_id)
        self.existing_repos.discard(repo_id)

    def create_repo(self, repo_id, exist_ok, private):
        self.existing_repos.add(repo_id)

    def upload_folder(self, repo_id, folder_path):
        self.uploaded.append((repo_id, folder_path))

    def create_collection(self, title, description, namespace, private, exists_ok):
        return FakeCreated()

    def add_collection_item(self, collection_slug, item_id, item_type, exists_ok):
        self.collection_items.append(item_id)


@pytest.fixture
def sample_config():
    return {
        "model_object": "FakeObject",
        "model_config": "fake_config",
        "quants": ["q4", "q6"],
        "collection": {"name": "Fibo", "description": "x", "version": "1.0.0"},
    }


@pytest.fixture(autouse=True)
def patch_mflux(monkeypatch):
    """Stand in for find_model_class + resolve_model_config without needing mflux installed."""
    import app.runner as runner_module

    monkeypatch.setattr(runner_module, "find_model_class", lambda model_object: FakeModel)
    monkeypatch.setattr(
        runner_module, "resolve_model_config", lambda name: f"model_config:{name}"
    )


def test_build_and_upload_one_quant_uploads(tmp_path, sample_config):
    api = FakeApi()
    result = build_and_upload_one_quant(
        sample_config, "q4", tmp_path, already_published=False, api=api
    )

    assert result == {
        "quant": "q4",
        "repo_id": "mflux-community/fibo-mflux-q4",
        "status": "uploaded",
    }
    assert len(api.uploaded) == 1
    # local build dir is cleaned up after upload
    assert not (tmp_path / "fibo-mflux-q4").exists()
    # and added to the collection
    assert api.collection_items == ["mflux-community/fibo-mflux-q4"]


def test_build_and_upload_one_quant_skips_existing(tmp_path, sample_config):
    api = FakeApi(existing_repos={"mflux-community/fibo-mflux-q4"})
    result = build_and_upload_one_quant(
        sample_config, "q4", tmp_path, already_published=True, api=api
    )

    assert result["status"] == "skipped_existing"
    assert len(api.uploaded) == 0
    # still added to the collection even when skipped (idempotent, no-op if already there)
    assert api.collection_items == ["mflux-community/fibo-mflux-q4"]


def test_build_and_upload_one_quant_force_overwrite_rebuilds_existing(tmp_path, sample_config):
    api = FakeApi(existing_repos={"mflux-community/fibo-mflux-q4"})
    result = build_and_upload_one_quant(
        sample_config,
        "q4",
        tmp_path,
        force_hf_overwrite=True,
        already_published=True,
        api=api,
    )

    assert result["status"] == "uploaded"
    assert "mflux-community/fibo-mflux-q4" in api.deleted


def test_build_and_upload_one_quant_reuses_valid_local_build(tmp_path, sample_config, monkeypatch):
    # Pre-seed a valid local build so build_quant should NOT be called.
    build_path = tmp_path / "fibo-mflux-q4"
    build_path.mkdir(parents=True)
    (build_path / "weights.bin").write_bytes(b"already-built")
    (build_path / "manifest.json").write_text(
        json.dumps({"sha256": hash_dir(build_path)})
    )

    calls = []
    original_build_quant = runner_module.build_quant

    def spy_build_quant(model_cls, model_config_obj, quant, build_path):
        calls.append(quant)
        return original_build_quant(model_cls, model_config_obj, quant, build_path)

    monkeypatch.setattr(runner_module, "build_quant", spy_build_quant)

    api = FakeApi()
    build_and_upload_one_quant(
        sample_config, "q4", tmp_path, already_published=False, api=api
    )

    assert calls == []  # reused, never rebuilt


def test_build_and_upload_one_quant_only_touches_its_own_quant(tmp_path, sample_config):
    """Confirms the split: a q4 job doesn't build or upload q6, even though
    sample_config declares both."""
    api = FakeApi()
    build_and_upload_one_quant(
        sample_config, "q4", tmp_path, already_published=False, api=api
    )

    uploaded_repo_ids = {repo_id for repo_id, _ in api.uploaded}
    assert uploaded_repo_ids == {"mflux-community/fibo-mflux-q4"}


def test_ensure_collection_adds_every_repo():
    api = FakeApi()
    config = {"collection": {"name": "Fibo", "description": "x"}}
    ensure_collection(api, config, ["mflux-community/fibo-mflux-q4", "mflux-community/fibo-mflux-q6"])

    assert api.collection_items == [
        "mflux-community/fibo-mflux-q4",
        "mflux-community/fibo-mflux-q6",
    ]
