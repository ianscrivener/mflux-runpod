from app.text_encoder_aliases import load_text_encoder_aliases


def test_load_text_encoder_aliases_missing_file_returns_empty(tmp_path):
    assert load_text_encoder_aliases(tmp_path / "nope.csv") == {}


def test_load_text_encoder_aliases_parses_and_strips_whitespace(tmp_path):
    csv_path = tmp_path / "text-encoder-alias.csv"
    csv_path.write_text(
        "text-encoder,alias\n"
        "Mistral3Model,Mistral 3\n"
        "SmolLM3ForCausalLM, Smol LM3 \n"
        "CLIPTextModel + T5EncoderModel, CLIP + T5\n"
    )

    assert load_text_encoder_aliases(csv_path) == {
        "Mistral3Model": "Mistral 3",
        "SmolLM3ForCausalLM": "Smol LM3",
        "CLIPTextModel + T5EncoderModel": "CLIP + T5",
    }


def test_load_text_encoder_aliases_corrupt_file_returns_empty(tmp_path):
    csv_path = tmp_path / "text-encoder-alias.csv"
    csv_path.write_text("not,even,close\nto,the,right,shape\n")
    assert load_text_encoder_aliases(csv_path) == {}


def test_load_text_encoder_aliases_skips_rows_missing_a_column(tmp_path):
    csv_path = tmp_path / "text-encoder-alias.csv"
    csv_path.write_text("text-encoder,alias\nQwen3Model,Qwen 3\n,\n")
    assert load_text_encoder_aliases(csv_path) == {"Qwen3Model": "Qwen 3"}
