# PRD

Two RunPod Flash services that convert and quantize AI models for MFlux, and keep the
`mflux-community` Hugging Face organization in sync. This replaces an earlier Modal-based
implementation (see `hf-mflux-community` repo) that became too expensive to run.

## Background: prior Modal implementation

Three scripts, ported/redesigned here:

- `2_hf_mflux-community_list/app.py` — scans the `mflux-community` HF org, writes a manifest
  of published models (name, size, upload date/user, commit hash).
- `1_modal-hf-model-cache/cpu-admin-hf-cache.py` — CPU-only; downloads/evicts HF repos into a
  persistent volume per a manifest.
- `3_modal-create-hf-models/create-mflux-models.py` — GPU (A10); reads a per-model config YAML
  (`configs/*.yaml`: `model_object`, `model_config`, `quants`, `collection`), builds each quant
  with mflux, uploads to its own HF repo, deletes the local build, groups quants into an HF
  Collection. Has crash-resume logic via a `manifest.json` sha256 hash per local build.

---

## (1) Orchestrator — CPU

A light CPU orchestrator that does the following:

1. Scans the MFlux-Community models and collections on HuggingFace (persists a manifest to
   HF — `models-hf`).
2. Scans the supported models from the MFlux GitHub repo (persists a manifest to
   HF — `models-mflux`).
3. Creates a missing models list from the two above (persists a manifest to
   HF — `models-missing`). A model series (e.g. `Qwen-Image`) counts as complete only when
   **every quant listed in its `configs/*.yaml`** (`q3, q4, q5, q6, q8, bf16`) has a live repo
   on HF — missing any quant marks that series as missing, for those quants only.
4. Applies manual overrides from a checked-in override file (e.g. `configs/overrides.yaml`) to
   force-include or force-exclude specific models from the auto-detected `models-missing` list.
5. Creates an ephemeral RunPod storage volume per missing model series and downloads the
   Hugging Face source weights into it, thus saving the GPU runner time for this task.
6. Calls a 2nd RunPod serverless process (the Runner) **asynchronously** — fire-and-forget
   trigger, returns immediately — to create the missing MFlux models. The Runner reports
   progress/completion back (e.g. updates the manifest itself, and/or the orchestrator polls
   `/report`). Updates manifests etc. on success & clears the ephemeral RunPod storage for that
   series once complete.

### Storage: ephemeral per-model-series HF volumes

- One RunPod Network Volume **per upstream model series** (e.g. one volume for `Qwen-Image`,
  not one global cache and not one per run).
- Created the first time a missing quant is found for that series.
- Holds both the downloaded source weights **and** in-progress/completed quantized build
  artifacts (mirrors the old `manifest.json` sha256 resume check in
  `create-mflux-models.py`), so a crashed run resumes without rebuilding finished quants.
- Deleted only once **all** quants for that series are uploaded and verified on HF.
- Once all quants are built and uploaded, the large downloaded **source weights are deleted**
  from the volume (build artifacts can remain until the volume itself is deleted per the rule
  above) to save storage cost.
- `force_hf_overwrite=true` (see `/generate` below) still reuses the volume and its local
  build cache (checked via sha256 manifest) — it only skips the "does this repo already exist
  on HF" short-circuit and replaces the Hub repo. It does not wipe the volume.

**API EndPoints**

```
/models_supported           models supported by MFlux app
/models_supported/update    run update & return same

/models_hf                  MFlux models on HF MFlux-Community 
/models_hf/update           run update & return same 

/models_missing             Missing Models

/generate_all               Generate all missing models. 

/generate                   Generate/regenerate one or more models. Params:
                               - hf_model_name        e.g. "Qwen/Qwen-Image-Edit"
                               - mflux_repo            default: https://github.com/mflux-community/mflux.git
                               - mflux_branch          default: main (also covers the old
                                                        /generate_with_branch use case: testing
                                                        new MFlux model support before it lands
                                                        on main)
                               - quants                e.g. ["q3","q4","q5","q6","q8","bf16"];
                                                        overrides the model's configs/*.yaml default
                               - force_hf_overwrite     default: false; replace existing HF repos
                                                        instead of skipping already-published quants
                             Request params override the matching configs/{model}.yaml fields for
                             this run; the config file remains the source of model_object/
                             model_config/collection metadata.

/report                     Report on recent model runs, etc. 
/health                     health check
/ping                       ping check

```

Note: `/generate_with_branch` is merged into `/generate` via the `mflux_repo`/`mflux_branch`
params above — no separate endpoint.

### Reporting: SQLite on a small persistent volume

The Orchestrator keeps a small persistent RunPod volume (a few GB, separate from the ephemeral
per-model-series volumes) holding `reports.sqlite` — a durable, queryable run history that
survives cold starts, independent of the HF manifests.

Two tables:

```
runs
  id              INTEGER PRIMARY KEY
  model_series    TEXT        -- e.g. "Qwen-Image"
  hf_model_name   TEXT        -- e.g. "Qwen/Qwen-Image-Edit"
  mflux_repo      TEXT
  mflux_branch    TEXT
  machine_type    TEXT        -- e.g. "NVIDIA_GEFORCE_RTX_4090"
  started_at      TEXT        -- ISO 8601
  finished_at     TEXT
  duration_s      REAL
  status          TEXT        -- running | success | failed | partial
  force_hf_overwrite INTEGER
  error           TEXT

quant_builds
  id                  INTEGER PRIMARY KEY
  run_id              INTEGER REFERENCES runs(id)
  quant               TEXT    -- q3 | q4 | q5 | q6 | q8 | bf16
  status              TEXT    -- built | uploaded | skipped_existing | failed
  total_size_bytes    INTEGER
  text_encoder_bytes  INTEGER
  transformer_bytes   INTEGER
  vae_bytes           INTEGER
  build_duration_s    REAL
  upload_duration_s   REAL
  hf_repo_id          TEXT    -- mflux-community/{variant}-mflux-{quant}
```

`/report` queries this DB (e.g. last N runs, per-model history, size/duration stats by quant
level) rather than re-deriving stats from HF on every call.

---

## (2) Runner — GPU

Triggered asynchronously by the Orchestrator's `/generate` or `/generate_all`. For a given
model series and quant list:

1. Reads `configs/{model}.yaml` for `model_object`, `model_config`, and `collection` metadata;
   request params (`quants`, `mflux_branch`, `force_hf_overwrite`) override file defaults for
   this run.
2. Installs mflux from `mflux_repo`@`mflux_branch`.
3. For each quant not yet on HF (or all quants, if `force_hf_overwrite=true`): builds the
   quantized model from the source weights already staged in the series' ephemeral volume by
   the Orchestrator, using the sha256 `manifest.json` check to skip quants already built
   locally in this volume.
4. Uploads each built quant to its own repo (`mflux-community/{variant}-mflux-{quant}`),
   deletes the local build from the volume once uploaded.
5. Groups all uploaded quants into an HF Collection (create-or-reuse).
6. Reports status back to the Orchestrator (for `/report` and manifest updates).

*(Runner API surface / GPU sizing / timeout budget: TBD.)*
