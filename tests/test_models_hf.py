import json

import app.models_hf as models_hf_module
from app.models_hf import load_models_hf, write_models_hf


def test_load_models_hf_missing_file_returns_empty(tmp_path):
    data_path = tmp_path / "models_hf.json"
    assert load_models_hf(data_path) == {"hf_models": []}


def test_write_then_load_models_hf_roundtrip(tmp_path):
    data_path = tmp_path / "models_hf.json"
    manifest = {
        "hf_models": [
            {
                "model_name": "mflux-community/qwen-image-mflux-q4",
                "size_gb": 12.3,
                "upload_date": "2026-01-01",
                "upload_user": "someone",
                "commit_hash": "abc123",
            }
        ]
    }

    write_models_hf(manifest, data_path)

    assert json.loads(data_path.read_text()) == manifest
    assert load_models_hf(data_path) == manifest


def test_write_models_hf_creates_parent_dir(tmp_path):
    data_path = tmp_path / "nested" / "models_hf.json"
    write_models_hf({"hf_models": []}, data_path)
    assert data_path.exists()


def test_load_models_hf_corrupt_file_returns_empty(tmp_path):
    """A truncated/corrupt manifest (crash mid-write, or a partial write on
    the network volume) must read as an empty manifest, not raise -- otherwise
    /models_hf and /models_missing 500 instead of degrading gracefully."""
    data_path = tmp_path / "models_hf.json"
    data_path.write_text("{not valid json")
    assert load_models_hf(data_path) == {"hf_models": []}


def test_default_data_path_resolved_at_call_time(tmp_path, monkeypatch):
    """DATA_PATH must be re-read on each call, not captured as an import-time
    default argument -- the deployed Orchestrator sets MODELS_HF_PATH to a
    path on its mounted volume, and tests monkeypatch it. (Same late-binding
    bug previously fixed in app/db.py.)"""
    target = tmp_path / "volume" / "models_hf.json"
    monkeypatch.setattr(models_hf_module, "DATA_PATH", target)

    manifest = {"hf_models": [{"model_name": "mflux-community/x-mflux-q4"}]}
    write_models_hf(manifest)  # no explicit data_path -- must use the patched DATA_PATH

    assert target.exists()
    assert load_models_hf() == manifest
