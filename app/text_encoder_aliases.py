"""Human-friendly aliases for raw text-encoder class names (PRD:
/text_encoder_aliases), used by the Models page's Text Encoder column.

data/text-encoder-alias.csv is hand-curated admin data, NOT synced via
app.hf_datasets (it's a local display preference, not upstream/derived
state). Two columns: the raw text-encoder name exactly as
app.models_src_details's `text_encoder` list renders once joined with
" + " (multi-encoder pipelines like FLUX's CLIP + T5 get one composite row,
e.g. "CLIPTextModel + T5EncoderModel"), and the alias to display instead.
"""

import csv
import os
from pathlib import Path

DATA_PATH = Path(
    os.environ.get(
        "TEXT_ENCODER_ALIAS_PATH",
        Path(__file__).resolve().parent.parent / "data" / "text-encoder-alias.csv",
    )
)


def load_text_encoder_aliases(data_path: Path | None = None) -> dict[str, str]:
    """{raw_name: alias}. A missing or corrupt file degrades to {} (no
    aliases -- callers fall back to showing the raw name), not an error."""
    data_path = data_path or DATA_PATH
    if not data_path.exists():
        return {}
    try:
        with open(data_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            return {
                row["text-encoder"].strip(): row["alias"].strip()
                for row in reader
                if row.get("text-encoder") and row.get("alias")
            }
    except (OSError, csv.Error, KeyError):
        return {}
