from app.models_mflux import load_models_mflux


def test_load_models_mflux_reads_real_data_file():
    models = load_models_mflux()
    assert isinstance(models, dict)
    assert "dev" in models
    assert models["dev"]["upstream"]["repo"] == "black-forest-labs/FLUX.1-dev"


def test_load_models_mflux_custom_path(tmp_path):
    data_path = tmp_path / "models_mflux.json"
    data_path.write_text('{"foo": {"model_type": "image"}}')

    models = load_models_mflux(data_path)
    assert models == {"foo": {"model_type": "image"}}
