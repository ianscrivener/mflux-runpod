"""SQLite-backed master tables for the models-catalog domain, derived from
the JSON caches in data-hf-sync/ (app.hf_datasets) plus configs/models/*.yaml.

Design: mflux_catalog / published_quants / source_repos mirror
data-hf-sync/models_mflux.json / models_hf.json / models_src_details.json
respectively (source_repos deduped by repo_id, since several config stems
can share one upstream repo -- e.g. Qwen-Image / Qwen-Image-2512). They live
in the SAME sqlite file as app/db.py's runs/quant_builds tables (app.db.
get_connection), but unlike those, they are a PURE DERIVED CACHE: every
rebuild does DROP TABLE + CREATE TABLE + repopulate from the JSON files, not
an incremental migration. That's deliberate -- these tables have already
gained columns twice in one session (size_text_encoder/size_transformers/
readme_meta), and treating them as disposable sidesteps ever writing a
migration for that churn. Never write anything here that isn't reconstructable
from the JSON files + configs/ -- if the JSON files are gone (a fresh
"ephemeral Orchestrator" checkout, restored from the HF bucket), a rebuild
regenerates these tables from scratch.

Rebuild trigger: rebuild_if_needed() compares an "input fingerprint" (the max
mtime across the three JSON files + every configs/models/*.yaml + overrides.yaml)
against what the last rebuild used, stored in catalog_build. This is
deliberately NOT tied to which refresh function ran -- app/main.py's
background loops gate their JSON refresh on staleness, so on a normal restart
with fresh-enough JSON the refresh functions never run, and a rebuild keyed
off "did update_models_hf() run" would never fire. Comparing file state
directly is correct regardless of *why* the JSON changed (a background
refresh, hf_sync's pull() overwriting it from the bucket, or a human editing
configs/models/*.yaml by hand). Every get_*() function below calls
rebuild_if_needed() itself, so a read is always self-healing -- this matters
for app/orchestrator_endpoint.py's Flash deployment, which has no lifespan/
background loops at all; without a read-time check it would serve empty
tables forever after a cold start.

/models_missing is NOT mirrored here -- GET /models_missing has always been
live-computed from configs/ + models_hf (never read back from
data-hf-sync/models_missing.json, which is a write-only publish snapshot),
and configs/models/*.yaml is authored source, not a sync cache, so there's
nothing to "stop reading from JSON" there. Only the hf_manifest input
(previously load_models_hf(), a JSON read) is swapped for
get_published_hf_manifest() (a SQLite read) -- compute_missing() itself is
unchanged.
"""

import json
import time
from pathlib import Path

from app.db import get_connection

CATALOG_BUILD_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_build (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    input_version  REAL NOT NULL,
    built_at       TEXT NOT NULL
);
"""

# Recreated in full on every rebuild -- see module docstring. A list of
# individual statements, not one executescript() blob: Cursor.executescript()
# implicitly COMMITs any open transaction before it runs, and applies no
# transaction wrapping of its own -- so the DROP+CREATE+INSERT sequence
# would NOT be atomic (a concurrent reader could see an empty table mid-
# rebuild), and it can't be safely nested inside our own BEGIN IMMEDIATE in
# rebuild_if_needed() either, since it would just commit that transaction
# out from under us. Individual execute() calls stay inside whatever
# transaction the caller already opened.
MIRROR_TABLES_STATEMENTS = [
    "DROP TABLE IF EXISTS mflux_catalog",
    """CREATE TABLE mflux_catalog (
        slug              TEXT PRIMARY KEY,
        model_type        TEXT,
        model_family      TEXT,
        model_sub_family  TEXT,
        model_aliases     TEXT NOT NULL,  -- JSON array
        upstream          TEXT NOT NULL,  -- JSON object; some entries carry extra keys
                                           -- beyond repo/license/status (controlnet_repo,
                                           -- custom_transformer_repo), so this is stored
                                           -- whole rather than flattened into columns
        mflux_cli         TEXT NOT NULL,  -- JSON array
        mflux_cli_tools   TEXT NOT NULL,  -- JSON array
        quants            TEXT             -- JSON array; NULL if the catalog entry has no quants key (non-image tools)
    )""",
    "DROP TABLE IF EXISTS published_quants",
    """CREATE TABLE published_quants (
        model_name   TEXT PRIMARY KEY,
        size_gb      REAL,
        upload_date  TEXT,
        upload_user  TEXT,
        commit_hash  TEXT
    )""",
    "DROP TABLE IF EXISTS source_repos",
    """CREATE TABLE source_repos (
        repo_id            TEXT PRIMARY KEY,
        size_gb            REAL,
        size_text_encoder  REAL,
        size_transformers  REAL,
        commit_hash        TEXT,
        last_modified      TEXT,
        text_encoder       TEXT,  -- JSON array; NULL if unknown (not a diffusers pipeline)
        readme_meta        TEXT,  -- JSON object; NULL if the repo has no card metadata
        error              TEXT   -- non-NULL only if this repo's scan failed
    )""",
]


def _ensure_build_table(conn) -> None:
    conn.execute(CATALOG_BUILD_SCHEMA)


def _input_fingerprint(
    mflux_path: Path,
    models_hf_path: Path,
    src_details_path: Path,
    configs_dir: Path,
    overrides_path: Path,
) -> float:
    """Max mtime across every file rebuild() actually reads. 0.0 if none of
    them exist yet (a from-scratch checkout with nothing pulled) -- a
    rebuild against that state just produces empty tables, which is correct."""
    mtimes = [
        p.stat().st_mtime
        for p in (mflux_path, models_hf_path, src_details_path, overrides_path)
        if p.exists()
    ]
    if configs_dir.exists():
        mtimes += [p.stat().st_mtime for p in configs_dir.glob("*.yaml")]
    return max(mtimes) if mtimes else 0.0


def _default_paths():
    import app.models_hf as models_hf_module
    import app.models_mflux as models_mflux_module
    import app.models_missing as models_missing_module
    import app.models_src_details as models_src_details_module

    return {
        "mflux_path": models_mflux_module.DATA_PATH,
        "models_hf_path": models_hf_module.DATA_PATH,
        "src_details_path": models_src_details_module.DATA_PATH,
        "configs_dir": models_missing_module.CONFIGS_DIR,
        "overrides_path": models_missing_module.OVERRIDES_PATH,
    }


def _rebuild(
    conn,
    fingerprint: float,
    mflux_path: Path,
    models_hf_path: Path,
    src_details_path: Path,
) -> None:
    from app.models_hf import load_models_hf
    from app.models_mflux import load_models_mflux
    from app.models_src_details import load_models_src_details

    # load_models_mflux() has no missing-file guard (unlike the other two,
    # which already degrade to {} on their own) -- a from-scratch checkout
    # before the first hf_sync pull shouldn't crash the rebuild.
    mflux = load_models_mflux(mflux_path) if mflux_path.exists() else {}
    hf_manifest = load_models_hf(models_hf_path)
    src_details = load_models_src_details(src_details_path)

    for statement in MIRROR_TABLES_STATEMENTS:
        conn.execute(statement)

    conn.executemany(
        """INSERT INTO mflux_catalog
           (slug, model_type, model_family, model_sub_family, model_aliases,
            upstream, mflux_cli, mflux_cli_tools, quants)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                slug,
                entry.get("model_type"),
                entry.get("model_family"),
                entry.get("model_sub_family"),
                json.dumps(entry.get("model_aliases", [])),
                json.dumps(entry.get("upstream", {})),
                json.dumps(entry.get("mflux_cli", [])),
                json.dumps(entry.get("mflux_cli_tools", [])),
                json.dumps(entry["quants"]) if "quants" in entry else None,
            )
            for slug, entry in mflux.items()
        ],
    )

    conn.executemany(
        """INSERT INTO published_quants
           (model_name, size_gb, upload_date, upload_user, commit_hash)
           VALUES (?, ?, ?, ?, ?)""",
        [
            (m["model_name"], m.get("size_gb"), m.get("upload_date"), m.get("upload_user"), m.get("commit_hash"))
            for m in hf_manifest.get("hf_models", [])
        ],
    )

    # Dedup by repo_id -- scan_models_src_details() already reuses one entry
    # across every stem that shares a source repo, so this is just collecting
    # the already-deduped values.
    seen_repos: dict[str, dict] = {}
    for entry in src_details.values():
        repo_id = entry.get("hf_model_name")
        if repo_id:
            seen_repos[repo_id] = entry
    conn.executemany(
        """INSERT INTO source_repos
           (repo_id, size_gb, size_text_encoder, size_transformers, commit_hash,
            last_modified, text_encoder, readme_meta, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                repo_id,
                entry.get("size_gb"),
                entry.get("size_text_encoder"),
                entry.get("size_transformers"),
                entry.get("commit_hash"),
                entry.get("last_modified"),
                json.dumps(entry["text_encoder"]) if entry.get("text_encoder") is not None else None,
                json.dumps(entry["readme_meta"]) if entry.get("readme_meta") is not None else None,
                entry.get("error"),
            )
            for repo_id, entry in seen_repos.items()
        ],
    )

    conn.execute(
        "INSERT INTO catalog_build (id, input_version, built_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET input_version = excluded.input_version, built_at = excluded.built_at",
        (fingerprint, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    )
    conn.commit()


def rebuild_if_needed(
    db_path: Path | None = None,
    mflux_path: Path | None = None,
    models_hf_path: Path | None = None,
    src_details_path: Path | None = None,
    configs_dir: Path | None = None,
    overrides_path: Path | None = None,
    force: bool = False,
) -> bool:
    """Rebuild the mirror tables if any watched input file changed since the
    last rebuild (or nothing's been built yet). Returns whether it rebuilt.
    Cheap when nothing changed -- a handful of stat() calls plus one indexed
    SELECT.

    The check-then-rebuild sequence runs inside one BEGIN IMMEDIATE
    transaction: every GET /models_* route is a sync `def` (Starlette runs
    those in its threadpool), so two requests can genuinely call this
    concurrently in different threads. BEGIN IMMEDIATE acquires the write
    lock up front and blocks a second concurrent caller until the first
    finishes; re-checking the fingerprint after acquiring the lock means
    that second caller then sees the now-current fingerprint and skips a
    redundant rebuild instead of racing to repeat it."""
    defaults = _default_paths()
    mflux_path = mflux_path or defaults["mflux_path"]
    models_hf_path = models_hf_path or defaults["models_hf_path"]
    src_details_path = src_details_path or defaults["src_details_path"]
    configs_dir = configs_dir or defaults["configs_dir"]
    overrides_path = overrides_path or defaults["overrides_path"]

    fingerprint = _input_fingerprint(mflux_path, models_hf_path, src_details_path, configs_dir, overrides_path)

    with get_connection(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_build_table(conn)
            row = conn.execute("SELECT input_version FROM catalog_build WHERE id = 1").fetchone()
            if not force and row is not None and row["input_version"] == fingerprint:
                conn.rollback()
                return False
            _rebuild(conn, fingerprint, mflux_path, models_hf_path, src_details_path)
            return True
        except Exception:
            conn.rollback()
            raise


def get_mflux_catalog(
    db_path: Path | None = None,
    mflux_path: Path | None = None,
    models_hf_path: Path | None = None,
    src_details_path: Path | None = None,
    configs_dir: Path | None = None,
    overrides_path: Path | None = None,
) -> dict:
    """Reconstructs the exact data-hf-sync/models_mflux.json shape: {slug: {...}}.
    Path overrides exist for test isolation -- normal callers only ever pass
    db_path (or nothing), and rebuild_if_needed resolves the rest itself."""
    rebuild_if_needed(
        db_path=db_path,
        mflux_path=mflux_path,
        models_hf_path=models_hf_path,
        src_details_path=src_details_path,
        configs_dir=configs_dir,
        overrides_path=overrides_path,
    )
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM mflux_catalog ORDER BY slug").fetchall()

    result = {}
    for row in rows:
        entry = {
            "model_type": row["model_type"],
            "model_family": row["model_family"],
            "model_sub_family": row["model_sub_family"],
            "model_aliases": json.loads(row["model_aliases"]),
            "upstream": json.loads(row["upstream"]),
            "mflux_cli": json.loads(row["mflux_cli"]),
            "mflux_cli_tools": json.loads(row["mflux_cli_tools"]),
        }
        if row["quants"] is not None:
            entry["quants"] = json.loads(row["quants"])
        result[row["slug"]] = entry
    return result


def get_published_hf_manifest(
    db_path: Path | None = None,
    mflux_path: Path | None = None,
    models_hf_path: Path | None = None,
    src_details_path: Path | None = None,
    configs_dir: Path | None = None,
    overrides_path: Path | None = None,
) -> dict:
    """Reconstructs the exact data-hf-sync/models_hf.json shape: {"hf_models": [...]}."""
    rebuild_if_needed(
        db_path=db_path,
        mflux_path=mflux_path,
        models_hf_path=models_hf_path,
        src_details_path=src_details_path,
        configs_dir=configs_dir,
        overrides_path=overrides_path,
    )
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM published_quants ORDER BY model_name").fetchall()

    return {
        "hf_models": [
            {
                "model_name": row["model_name"],
                "size_gb": row["size_gb"],
                "upload_date": row["upload_date"],
                "upload_user": row["upload_user"],
                "commit_hash": row["commit_hash"],
            }
            for row in rows
        ]
    }


def get_source_repos(
    db_path: Path | None = None,
    mflux_path: Path | None = None,
    models_hf_path: Path | None = None,
    src_details_path: Path | None = None,
    configs_dir: Path | None = None,
    overrides_path: Path | None = None,
) -> dict:
    """{repo_id: {...}} for every scanned source repo (deduped)."""
    rebuild_if_needed(
        db_path=db_path,
        mflux_path=mflux_path,
        models_hf_path=models_hf_path,
        src_details_path=src_details_path,
        configs_dir=configs_dir,
        overrides_path=overrides_path,
    )
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM source_repos ORDER BY repo_id").fetchall()

    result = {}
    for row in rows:
        if row["error"] is not None:
            result[row["repo_id"]] = {"hf_model_name": row["repo_id"], "error": row["error"]}
            continue
        result[row["repo_id"]] = {
            "hf_model_name": row["repo_id"],
            "size_gb": row["size_gb"],
            "size_text_encoder": row["size_text_encoder"],
            "size_transformers": row["size_transformers"],
            "commit_hash": row["commit_hash"],
            "last_modified": row["last_modified"],
            "text_encoder": json.loads(row["text_encoder"]) if row["text_encoder"] is not None else None,
            "readme_meta": json.loads(row["readme_meta"]) if row["readme_meta"] is not None else None,
        }
    return result


def get_models_src_details(
    configs: dict[str, dict] | None = None,
    db_path: Path | None = None,
    mflux_path: Path | None = None,
    models_hf_path: Path | None = None,
    src_details_path: Path | None = None,
    configs_dir: Path | None = None,
    overrides_path: Path | None = None,
) -> dict:
    """Reconstructs the exact data-hf-sync/models_src_details.json shape:
    {config_stem: {...}} -- joins configs/models/*.yaml's hf_model_name
    against source_repos, same as scan_models_src_details() does directly
    against the HF API. configs/*.yaml is authored source, read live (not a
    sync cache), matching /models_missing's existing behavior."""
    if configs is None:
        from app.models_missing import load_configs

        configs = load_configs()

    repos = get_source_repos(
        db_path=db_path,
        mflux_path=mflux_path,
        models_hf_path=models_hf_path,
        src_details_path=src_details_path,
        configs_dir=configs_dir,
        overrides_path=overrides_path,
    )

    result = {}
    for stem, config in configs.items():
        repo_id = config.get("hf_model_name")
        if repo_id and repo_id in repos:
            result[stem] = repos[repo_id]
    return result


def resolve_model_slug(config: dict, mflux_catalog: dict) -> str | None:
    """Authoritative configs/models/*.yaml stem -> models_mflux.json catalog
    slug resolution. Both live in independent namespaces that frequently
    diverge -- confirmed live, 2026-08-20: slugifying a config's
    collection.name (the old client-side approach the webapp used) missed
    the correct family for 12 of 21 models (e.g. "Flux.1 Dev" slugifies to
    "flux-1-dev", but the upstream catalog's key for that exact model is
    just "dev").

    Primary: config["model_config"] (its literal mflux CLI config name,
    underscore-separated) normalized to the catalog's hyphenated key
    convention -- resolves most models live. A second normalization is tried
    when the plain one misses: some catalog slugs embed a literal dot in a
    version number (e.g. "z-image-turbo-controlnet-union-2.1"), which
    mflux's Python method-name convention can't represent (dots aren't legal
    in identifiers, so mflux spells it "z_image_turbo_controlnet_union_2_1")
    -- a blind underscore->hyphen replace produces "...-2-1", never matching
    the real "...-2.1" slug. Confirmed live 2026-08-20: without this, that
    config silently fell through to the upstream.repo fallback below and
    resolved to its shorter sibling "z-image-turbo" instead -- wrong
    sub_family, not a crash, so easy to miss. Fallback: match
    config["hf_model_name"] against catalog entries' upstream.repo (several
    catalog entries -- a base model plus its ControlNet/Fill/etc. variants --
    can share one upstream repo, so the SHORTEST matching slug is taken as
    the base/canonical one). None if nothing resolves, meaning the model
    genuinely isn't in the upstream catalog yet (a real gap, not a matching
    failure)."""
    model_config = config.get("model_config")
    if model_config:
        candidates = [model_config.replace("_", "-")]
        if "_" in model_config:
            base, _, last = model_config.rpartition("_")
            candidates.append(f"{base.replace('_', '-')}.{last}")
        for key in candidates:
            if key in mflux_catalog:
                return key

    hf_model_name = config.get("hf_model_name")
    if hf_model_name:
        candidates = [
            slug
            for slug, entry in mflux_catalog.items()
            if (entry.get("upstream") or {}).get("repo") == hf_model_name
        ]
        if candidates:
            return min(candidates, key=len)

    return None


def get_model_identities(
    configs: dict[str, dict] | None = None,
    db_path: Path | None = None,
    mflux_path: Path | None = None,
    models_hf_path: Path | None = None,
    src_details_path: Path | None = None,
    configs_dir: Path | None = None,
    overrides_path: Path | None = None,
) -> dict:
    """{config_stem: {slug, hf_repo_slug, model_type, model_family,
    model_sub_family, quants}} for every configs/models/*.yaml -- the
    authoritative server-side slug/family resolution the Models page needs
    (neither /models_missing nor /models_mflux exposes it directly).

    Two DIFFERENT slugs, deliberately kept separate:
    - `slug`: the models_mflux.json catalog's own key for this model (via
      resolve_model_slug -- see its docstring). Used to look up
      family/type/sub_family. Sometimes None (model not yet in the upstream
      catalog) -- a real gap, not a lookup bug.
    - `hf_repo_slug`: slugify(config["collection"]["name"]), the slug WE use
      when naming our own published mflux-community/{slug}-mflux-{quant}
      repos (same computation app/models_missing.py's expected_repo_ids()
      already does). Always present -- pure function of the config, no
      matching involved -- used for the "search our published repos" link.
      Confirmed live, 2026-08-20: these two slugs differ for most models
      (e.g. catalog key "dev" vs. our own publish slug "flux-1-dev" for the
      exact same FLUX.1-dev model) -- conflating them was the original bug.

    `quants` comes from the config itself (config["quants"] -- the same
    field compute_missing() already treats as authoritative), NOT the
    catalog's own quants field, which is missing for some models that are
    genuinely buildable locally (confirmed live: every Fibo-Edit* catalog
    entry lacks a quants key even though their configs declare one) --
    distinct from /models_missing's per-stem missing_quants (a subset of
    this)."""
    if configs is None:
        from app.models_missing import load_configs

        configs = load_configs()

    from app.models_missing import slugify

    catalog = get_mflux_catalog(
        db_path=db_path,
        mflux_path=mflux_path,
        models_hf_path=models_hf_path,
        src_details_path=src_details_path,
        configs_dir=configs_dir,
        overrides_path=overrides_path,
    )

    result = {}
    for stem, config in configs.items():
        slug = resolve_model_slug(config, catalog)
        entry = catalog.get(slug) if slug else None
        collection_name = (config.get("collection") or {}).get("name")
        result[stem] = {
            "hf_repo_slug": slugify(collection_name) if collection_name else None,
            "slug": slug,
            "model_type": entry.get("model_type") if entry else None,
            "model_family": entry.get("model_family") if entry else None,
            "model_sub_family": entry.get("model_sub_family") if entry else None,
            "quants": config.get("quants"),
        }
    return result


def compute_available_models(
    catalog: dict,
    configs: dict[str, dict],
    hf_manifest: dict,
    skip_rules: dict,
) -> dict:
    """The new canonical 'what models + quants are available' enumeration:
    every models_mflux.json catalog entry, assigned the fixed
    models_missing.DEFAULT_QUANTS set (replacing configs/*.yaml's own
    per-model `quants` field), minus whatever data-hf-sync/models_skipped.json
    excludes (see app.models_missing.load_models_skipped for its shape).
    compute_missing() is untouched and still backs real dispatch
    (generate_one) -- this is the informational, broader-than-dispatchable
    listing the Models/Summary pages show.

    Returns {id: {...}} where `id` is the configs/models/*.yaml stem when one
    resolves to this catalog slug (via resolve_model_slug, reversed), else
    the catalog slug itself for catalog-only entries with no local config yet
    (`buildable: False` -- generate_one()/POST /models_queue both require a
    real config to dispatch, so these are display-only; the frontend must not
    let their missing quants be queued).

    Published-repo matching: config-backed entries keep the existing
    hf_repo_slug (slugify(collection.name)) so already-published quants for
    the 21 tracked models keep matching exactly as before compute_missing()
    always did. Catalog-only entries have no collection.name to slugify yet,
    so they fall back to the catalog slug itself as the publish slug --
    verified live 2026-08-20 that every one of the 75 currently-published
    repo names resolves via a config's hf_repo_slug, none needed this
    fallback, so it never misclassifies an already-published quant.

    Enumeration is CONFIG-driven first, catalog-only second -- NOT a single
    pass over catalog.items(). Two real cases break a catalog-driven pass:
    multiple config stems can resolve to the SAME catalog slug (e.g.
    Qwen-Image-2512/Qwen-Image-Edit-2509/Qwen-Image-Edit-2511 all resolve to
    one of two catalog entries), and a config can resolve to NO catalog slug
    at all (Qwen-Image-Layered isn't in models_mflux.json yet -- a real gap,
    same as resolve_model_slug's docstring describes). A catalog-keyed loop
    silently drops every stem but the last one written into a slug->stem
    reverse index, and drops unresolvable configs entirely -- confirmed live
    2026-08-20 by diffing against compute_missing()'s published-quant
    classification for all 21 tracked configs before landing this two-pass
    version."""
    from app.models_hf import is_actually_published
    from app.models_missing import DEFAULT_QUANTS, HF_ORG, slugify

    published = {
        m["model_name"] for m in hf_manifest.get("hf_models", []) if is_actually_published(m)
    }

    skipped_families = skip_rules.get("families", set())
    skipped_sub_families = skip_rules.get("sub_families", set())
    skipped_models = skip_rules.get("models", set())
    skipped_quants = skip_rules.get("quants", {})

    def catalog_entry_skipped(slug: str, entry: dict) -> bool:
        if slug.lower() in skipped_models:
            return True
        family = (entry.get("model_family") or "").lower()
        sub_family = (entry.get("model_sub_family") or "").lower()
        return family in skipped_families or sub_family in skipped_sub_families

    def build_entry(*, catalog_slug, stem, buildable, publish_slug, entry) -> dict:
        quants = [q for q in DEFAULT_QUANTS if q not in set(skipped_quants.get(catalog_slug, []))]
        expected_repo_ids = {q: f"{HF_ORG}/{publish_slug}-mflux-{q}" for q in quants}
        missing_quants = [q for q, repo_id in expected_repo_ids.items() if repo_id not in published]
        return {
            "catalog_slug": catalog_slug,
            "stem": stem,
            "buildable": buildable,
            "hf_repo_slug": publish_slug,
            "model_type": entry.get("model_type"),
            "model_family": entry.get("model_family"),
            "model_sub_family": entry.get("model_sub_family"),
            "quants": quants,
            "missing_quants": missing_quants,
            "expected_repo_ids": expected_repo_ids,
        }

    result: dict[str, dict] = {}
    resolved_slugs: set[str] = set()

    for stem, config in configs.items():
        slug = resolve_model_slug(config, catalog)
        entry = catalog.get(slug, {}) if slug else {}
        if slug:
            resolved_slugs.add(slug)
        # skipped_models entries can name either a catalog slug (the normal
        # case) or a config stem -- the latter is the only way to skip a
        # config with no catalog match at all (slug is None, so
        # catalog_entry_skipped has no family/sub_family/slug to check
        # against; e.g. Qwen-Image-Layered isn't in models_mflux.json yet).
        if (slug and catalog_entry_skipped(slug, entry)) or stem.lower() in skipped_models:
            continue

        collection_name = (config.get("collection") or {}).get("name")
        publish_slug = slugify(collection_name) if collection_name else (slug or stem)
        result[stem] = build_entry(
            catalog_slug=slug, stem=stem, buildable=True, publish_slug=publish_slug, entry=entry
        )

    for slug, entry in catalog.items():
        if slug in resolved_slugs or catalog_entry_skipped(slug, entry):
            continue
        result[slug] = build_entry(
            catalog_slug=slug, stem=None, buildable=False, publish_slug=slug, entry=entry
        )

    return result


def get_available_models(
    configs: dict[str, dict] | None = None,
    db_path: Path | None = None,
    mflux_path: Path | None = None,
    models_hf_path: Path | None = None,
    src_details_path: Path | None = None,
    configs_dir: Path | None = None,
    overrides_path: Path | None = None,
    skipped_path: Path | None = None,
) -> dict:
    """Self-healing wrapper around compute_available_models() -- see its
    docstring for the actual logic. `skipped_path` is read live (like
    configs/*.yaml and overrides.yaml), not mirrored into a SQLite table:
    it doesn't affect mflux_catalog/published_quants/source_repos at all, so
    there's nothing to rebuild on its account -- every call already reflects
    the current file. POST /models_skipped/refresh (app.main) exists anyway,
    per the user's explicit ask, as a tangible confirm-it-took-effect action
    in the Admin menu; it just force-rebuilds the other mirror tables too."""
    from app.models_missing import MODELS_SKIPPED_PATH, load_models_skipped

    if configs is None:
        from app.models_missing import load_configs

        configs = load_configs()

    paths = dict(
        db_path=db_path,
        mflux_path=mflux_path,
        models_hf_path=models_hf_path,
        src_details_path=src_details_path,
        configs_dir=configs_dir,
        overrides_path=overrides_path,
    )
    catalog = get_mflux_catalog(**paths)
    hf_manifest = get_published_hf_manifest(**paths)
    skip_rules = load_models_skipped(skipped_path or MODELS_SKIPPED_PATH)

    return compute_available_models(catalog, configs, hf_manifest, skip_rules)
