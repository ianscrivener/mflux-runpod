import json

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
