# Templates (not yet built)

Placeholder for the collection/model-card templating work discussed
2026-08-19: deriving `collection.description`/`collection.version` (and
eventually per-model HF model-card content) from a template instead of
hand-duplicating near-identical text across every `configs/models/*.yaml`
file — verified live that description/version already follow one fixed
pattern exactly in 19/21 configs; the other 2 each have a single-field
mismatch, and both are typos, not real customization: `Fibo-Edit-RMBG.yaml`
(`version: 1.0` instead of `1.0.0`) and `Fibo-lite.yaml` (description text
doesn't match the template).

Planned:

- `collection-card-template.json` — template for the `collection` block
  currently hand-typed in every `configs/models/*.yaml` file.
- `model-card-template.json` — template for the HF model-card content
  written into each published quant repo.

Nice-to-have, not essential — flagged so it isn't forgotten, not built yet.
Once this exists, `configs/models/*.yaml` itself is expected to shrink
further (see the 2026-08-19 discussion on redundant fields).
