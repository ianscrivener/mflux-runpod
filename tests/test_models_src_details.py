import json
from types import SimpleNamespace

import app.models_src_details as models_src_details_module
from app.models_src_details import (
    _component_size_bytes,
    extract_text_encoders,
    load_models_src_details,
    write_models_src_details,
)


def _sibling(rfilename, size):
    return SimpleNamespace(rfilename=rfilename, size=size)


def test_component_size_bytes_sums_matching_top_level_folder():
    info = SimpleNamespace(
        siblings=[
            _sibling("text_encoder/config.json", 100),
            _sibling("text_encoder/model.safetensors", 900),
            _sibling("text_encoder_2/model.safetensors", 500),
            _sibling("transformer/config.json", 50),
            _sibling("README.md", 10),
        ]
    )
    assert _component_size_bytes(info, "text_encoder") == 1500
    assert _component_size_bytes(info, "transformer") == 50


def test_component_size_bytes_returns_zero_when_no_matching_folder():
    info = SimpleNamespace(siblings=[_sibling("README.md", 10), _sibling("config.json", 5)])
    assert _component_size_bytes(info, "text_encoder") == 0


def test_component_size_bytes_skips_siblings_with_no_size():
    info = SimpleNamespace(siblings=[_sibling("text_encoder/pointer.bin", None)])
    assert _component_size_bytes(info, "text_encoder") == 0


def test_extract_text_encoders_flux_style_pipeline():
    model_index = {
        "_class_name": "FluxPipeline",
        "scheduler": ["diffusers", "FlowMatchEulerDiscreteScheduler"],
        "text_encoder": ["transformers", "CLIPTextModel"],
        "text_encoder_2": ["transformers", "T5EncoderModel"],
        "transformer": ["diffusers", "FluxTransformer2DModel"],
    }
    assert extract_text_encoders(model_index) == ["CLIPTextModel", "T5EncoderModel"]


def test_extract_text_encoders_no_text_encoder_keys_returns_none():
    model_index = {"_class_name": "SomePipeline", "vae": ["diffusers", "AutoencoderKL"]}
    assert extract_text_encoders(model_index) is None


def test_extract_text_encoders_ignores_malformed_entries():
    model_index = {"text_encoder": "not-a-list", "text_encoder_2": []}
    assert extract_text_encoders(model_index) is None


def test_load_models_src_details_missing_file_returns_empty_dict(tmp_path):
    data_path = tmp_path / "models_src_details.json"
    assert load_models_src_details(data_path) == {}


def test_write_then_load_models_src_details_roundtrip(tmp_path):
    data_path = tmp_path / "models_src_details.json"
    data = {
        "Fibo": {
            "hf_model_name": "briaai/FIBO",
            "size_gb": 12.34,
            "commit_hash": "abc123",
            "last_modified": "2026-01-01T00:00:00+00:00",
            "text_encoder": ["Qwen3"],
        }
    }

    write_models_src_details(data, data_path)

    assert json.loads(data_path.read_text()) == data
    assert load_models_src_details(data_path) == data


def test_write_models_src_details_creates_parent_dir(tmp_path):
    data_path = tmp_path / "nested" / "models_src_details.json"
    write_models_src_details({}, data_path)
    assert data_path.exists()


def test_load_models_src_details_corrupt_file_returns_empty_dict(tmp_path):
    data_path = tmp_path / "models_src_details.json"
    data_path.write_text("{not valid json")
    assert load_models_src_details(data_path) == {}


def test_default_data_path_resolved_at_call_time(tmp_path, monkeypatch):
    """DATA_PATH must be re-read on each call, not captured as an import-time
    default argument -- the deployed Orchestrator sets MODELS_SRC_DETAILS_PATH
    to a path on its mounted volume. (Same late-binding bug previously fixed
    in app/db.py and app/models_hf.py.)"""
    target = tmp_path / "volume" / "models_src_details.json"
    monkeypatch.setattr(models_src_details_module, "DATA_PATH", target)

    data = {"Fibo": {"hf_model_name": "briaai/FIBO"}}
    write_models_src_details(data)  # no explicit data_path -- must use the patched DATA_PATH

    assert target.exists()
    assert load_models_src_details() == data
