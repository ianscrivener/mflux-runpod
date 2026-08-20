import json
import os

import pytest

from app.models_catalog import (
    compute_available_models,
    get_available_models,
    get_mflux_catalog,
    get_model_identities,
    get_models_src_details,
    get_published_hf_manifest,
    get_source_repos,
    rebuild_if_needed,
    resolve_model_slug,
)

EMPTY_SKIP_RULES = {"families": set(), "sub_families": set(), "models": set(), "quants": {}}


@pytest.fixture
def paths(tmp_path):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    return {
        "db_path": tmp_path / "catalog.sqlite",
        "mflux_path": tmp_path / "models_mflux.json",
        "models_hf_path": tmp_path / "models_hf.json",
        "src_details_path": tmp_path / "models_src_details.json",
        "configs_dir": configs_dir,
        "overrides_path": tmp_path / "overrides.yaml",
    }


def _write_json(path, data):
    path.write_text(json.dumps(data))


def test_rebuild_if_needed_on_empty_state_creates_empty_tables(paths):
    assert rebuild_if_needed(**paths) is True
    assert get_mflux_catalog(**paths) == {}
    assert get_published_hf_manifest(**paths) == {"hf_models": []}
    assert get_source_repos(**paths) == {}


def test_rebuild_if_needed_second_call_is_a_noop_when_nothing_changed(paths):
    assert rebuild_if_needed(**paths) is True
    assert rebuild_if_needed(**paths) is False


def test_rebuild_if_needed_detects_a_changed_input_file(paths, tmp_path):
    _write_json(paths["mflux_path"], {"foo": {"model_type": "image", "model_aliases": [], "mflux_cli": [], "mflux_cli_tools": [], "upstream": {}}})
    assert rebuild_if_needed(**paths) is True
    assert rebuild_if_needed(**paths) is False

    # Touch the file with new content + a deterministically later mtime --
    # must be seen as changed. os.utime() rather than a sleep-then-write:
    # some filesystems' mtime resolution is coarse enough that a short sleep
    # doesn't guarantee the new write actually lands on a later timestamp.
    _write_json(paths["mflux_path"], {"foo": {"model_type": "image", "model_aliases": [], "mflux_cli": [], "mflux_cli_tools": [], "upstream": {}}, "bar": {"model_type": "image", "model_aliases": [], "mflux_cli": [], "mflux_cli_tools": [], "upstream": {}}})
    later = paths["mflux_path"].stat().st_mtime + 1
    os.utime(paths["mflux_path"], (later, later))
    assert rebuild_if_needed(**paths) is True
    assert get_mflux_catalog(**paths) == {
        "foo": {"model_type": "image", "model_family": None, "model_sub_family": None, "model_aliases": [], "upstream": {}, "mflux_cli": [], "mflux_cli_tools": []},
        "bar": {"model_type": "image", "model_family": None, "model_sub_family": None, "model_aliases": [], "upstream": {}, "mflux_cli": [], "mflux_cli_tools": []},
    }


def test_get_mflux_catalog_roundtrip_including_optional_quants_key(paths):
    _write_json(
        paths["mflux_path"],
        {
            "fibo": {
                "model_type": "image",
                "model_family": "fibo",
                "model_sub_family": "fibo",
                "model_aliases": ["fibo"],
                "upstream": {"repo": "briaai/FIBO", "license": "cc-by-nc-4.0", "status": "active"},
                "mflux_cli": ["mflux-generate-fibo"],
                "mflux_cli_tools": [],
                "quants": ["q3", "q4"],
            },
            "depth-pro": {
                "model_type": "depth-estimation",
                "model_family": "depth-pro",
                "model_sub_family": "depth-pro",
                "model_aliases": [],
                "upstream": {"repo": "apple/DepthPro", "license": None, "status": "active"},
                "mflux_cli": [],
                "mflux_cli_tools": ["mflux-save-depth"],
                # no "quants" key -- not a quantized image model
            },
        },
    )

    result = get_mflux_catalog(**paths)

    assert "quants" in result["fibo"]
    assert result["fibo"]["quants"] == ["q3", "q4"]
    assert "quants" not in result["depth-pro"]


def test_get_published_hf_manifest_roundtrip(paths):
    _write_json(
        paths["models_hf_path"],
        {
            "hf_models": [
                {
                    "model_name": "mflux-community/fibo-mflux-q4",
                    "size_gb": 12.3,
                    "upload_date": "2026-01-01",
                    "upload_user": "someone",
                    "commit_hash": "abc123",
                }
            ]
        },
    )

    result = get_published_hf_manifest(**paths)

    assert result == {
        "hf_models": [
            {
                "model_name": "mflux-community/fibo-mflux-q4",
                "size_gb": 12.3,
                "upload_date": "2026-01-01",
                "upload_user": "someone",
                "commit_hash": "abc123",
            }
        ]
    }


def test_source_repos_dedupes_by_repo_id(paths):
    """Two config stems sharing one upstream repo (e.g. Qwen-Image /
    Qwen-Image-2512) must collapse to one source_repos row, not two."""
    shared = {
        "hf_model_name": "Qwen/Qwen-Image-2512",
        "size_gb": 57.7,
        "size_text_encoder": 16.58,
        "size_transformers": 40.86,
        "commit_hash": "abc",
        "last_modified": "2026-01-01T00:00:00+00:00",
        "text_encoder": ["Qwen2_5_VLForConditionalGeneration"],
        "readme_meta": {"license": "apache-2.0"},
    }
    _write_json(
        paths["src_details_path"],
        {"Qwen-Image": shared, "Qwen-Image-2512": shared},
    )

    repos = get_source_repos(**paths)

    assert list(repos.keys()) == ["Qwen/Qwen-Image-2512"]
    assert repos["Qwen/Qwen-Image-2512"]["readme_meta"] == {"license": "apache-2.0"}


def test_source_repos_preserves_error_shape(paths):
    _write_json(
        paths["src_details_path"],
        {"Broken": {"hf_model_name": "some/gated-repo", "error": "401 Client Error"}},
    )

    repos = get_source_repos(**paths)

    assert repos == {"some/gated-repo": {"hf_model_name": "some/gated-repo", "error": "401 Client Error"}}


def test_get_models_src_details_joins_configs_against_source_repos(paths):
    _write_json(
        paths["src_details_path"],
        {
            "Fibo": {
                "hf_model_name": "briaai/FIBO",
                "size_gb": 25.61,
                "size_text_encoder": 6.15,
                "size_transformers": 16.57,
                "commit_hash": "abc",
                "last_modified": "2026-01-01T00:00:00+00:00",
                "text_encoder": ["SmolLM3ForCausalLM"],
                "readme_meta": None,
            }
        },
    )
    configs = {"Fibo": {"hf_model_name": "briaai/FIBO"}, "NoSource": {}}

    # No explicit rebuild_if_needed() call -- get_models_src_details must
    # trigger its own rebuild (via get_source_repos) from the given paths.
    # This is what keeps app/orchestrator_endpoint.py's lifespan-less Flash
    # routes correct: they never call rebuild_if_needed() themselves.
    result = get_models_src_details(configs=configs, **paths)

    assert set(result) == {"Fibo"}
    assert result["Fibo"]["size_gb"] == 25.61


def test_resolve_model_slug_prefers_model_config_normalized():
    catalog = {"ernie-image": {"model_family": "ernie-image"}}
    config = {"model_config": "ernie_image", "hf_model_name": "baidu/ERNIE-Image"}
    assert resolve_model_slug(config, catalog) == "ernie-image"


def test_resolve_model_slug_handles_dotted_version_number_in_catalog_slug():
    """A catalog slug can embed a literal dot in a version number
    ("z-image-turbo-controlnet-union-2.1"), which mflux's Python method-name
    convention can't represent (no dots in identifiers), so the config's
    model_config spells it "..._2_1". A blind underscore->hyphen replace
    alone produces "...-2-1" and misses -- must also try dotting the final
    underscore-separated segment."""
    catalog = {
        "z-image-turbo": {"upstream": {"repo": "Tongyi-MAI/Z-Image-Turbo"}},
        "z-image-turbo-controlnet-union-2.1": {"upstream": {"repo": "Tongyi-MAI/Z-Image-Turbo"}},
    }
    config = {
        "model_config": "z_image_turbo_controlnet_union_2_1",
        "hf_model_name": "Tongyi-MAI/Z-Image-Turbo",
    }
    # Without the dotted-candidate fix this would fall through to the
    # upstream.repo fallback and wrongly resolve to the shorter sibling.
    assert resolve_model_slug(config, catalog) == "z-image-turbo-controlnet-union-2.1"


def test_resolve_model_slug_falls_back_to_shortest_upstream_repo_match():
    """model_config doesn't hit the catalog directly -- fall back to
    hf_model_name/upstream.repo, preferring the shortest (base) slug over
    ControlNet/Fill variants that share the same source repo."""
    catalog = {
        "dev": {"upstream": {"repo": "black-forest-labs/FLUX.1-dev"}},
        "dev-controlnet-canny": {"upstream": {"repo": "black-forest-labs/FLUX.1-dev"}},
    }
    config = {"model_config": "flux1_dev_nonexistent", "hf_model_name": "black-forest-labs/FLUX.1-dev"}
    assert resolve_model_slug(config, catalog) == "dev"


def test_resolve_model_slug_returns_none_when_genuinely_not_in_catalog():
    catalog = {"other-model": {}}
    config = {"model_config": "not_there", "hf_model_name": "someone/not-there-either"}
    assert resolve_model_slug(config, catalog) is None


def test_get_model_identities_end_to_end(paths):
    _write_json(
        paths["mflux_path"],
        {
            "ernie-image": {
                "model_type": "image",
                "model_family": "ernie-image",
                "model_sub_family": "ernie-image",
                "model_aliases": [],
                "upstream": {"repo": "baidu/ERNIE-Image"},
                "mflux_cli": [],
                "mflux_cli_tools": [],
                "quants": ["q3", "q4"],
            }
        },
    )
    configs = {
        "ERNIE-Image-Base": {
            "model_config": "ernie_image",
            "hf_model_name": "baidu/ERNIE-Image",
            "quants": ["q3", "q4", "q6"],
            "collection": {"name": "ERNIE-Image Base"},
        },
        "Unreleased-Model": {"model_config": "not_yet_published"},
    }

    result = get_model_identities(configs=configs, **paths)

    # quants comes from the config, not the catalog entry (which here
    # declares a different set, ["q3", "q4"]) -- see get_model_identities'
    # docstring for why the config is treated as authoritative. hf_repo_slug
    # is slugify(collection.name) -- our OWN publish-repo naming, distinct
    # from the catalog's "slug" key (confirmed live these genuinely differ).
    assert result["ERNIE-Image-Base"] == {
        "hf_repo_slug": "ernie-image-base",
        "slug": "ernie-image",
        "model_type": "image",
        "model_family": "ernie-image",
        "model_sub_family": "ernie-image",
        "quants": ["q3", "q4", "q6"],
    }
    assert result["Unreleased-Model"] == {
        "hf_repo_slug": None,
        "slug": None,
        "model_type": None,
        "model_family": None,
        "model_sub_family": None,
        "quants": None,
    }


DEV_CATALOG = {
    "dev": {"model_type": "image", "model_family": "flux1", "model_sub_family": "flux1-dev"},
    "schnell": {"model_type": "image", "model_family": "flux1", "model_sub_family": "flux1-schnell"},
    "qwen-image": {"model_type": "image", "model_family": "qwen-image", "model_sub_family": "qwen-image"},
    "flux2-klein-9b-kv": {"model_type": "image", "model_family": "flux2", "model_sub_family": "flux2-klein"},
    "boogu-image-turbo": {"model_type": "image", "model_family": "boogu", "model_sub_family": "boogu-image-turbo"},
}


def test_compute_available_models_config_backed_row_uses_hf_repo_slug_and_default_quants():
    configs = {
        "Flux.1-Dev": {"model_config": "dev", "collection": {"name": "Flux.1 Dev"}},
    }
    hf_manifest = {"hf_models": [{"model_name": "mflux-community/flux-1-dev-mflux-q4"}]}

    result = compute_available_models(DEV_CATALOG, configs, hf_manifest, EMPTY_SKIP_RULES)

    entry = result["Flux.1-Dev"]
    assert entry["buildable"] is True
    assert entry["stem"] == "Flux.1-Dev"
    assert entry["catalog_slug"] == "dev"
    assert entry["hf_repo_slug"] == "flux-1-dev"  # NOT the catalog slug "dev"
    assert entry["quants"] == ["q3", "q4", "q5", "q6", "q8", "bf16"]
    assert "q4" not in entry["missing_quants"]  # published, matched via hf_repo_slug
    assert set(entry["missing_quants"]) == {"q3", "q5", "q6", "q8", "bf16"}


def test_compute_available_models_includes_catalog_only_entries_as_not_buildable():
    """A catalog entry with no matching configs/models/*.yaml still shows up
    -- informational, not queueable -- keyed by its own catalog slug."""
    result = compute_available_models(DEV_CATALOG, {}, {"hf_models": []}, EMPTY_SKIP_RULES)

    assert "boogu-image-turbo" in result
    entry = result["boogu-image-turbo"]
    assert entry["buildable"] is False
    assert entry["stem"] is None
    assert entry["hf_repo_slug"] == "boogu-image-turbo"  # falls back to the catalog slug


def test_compute_available_models_multiple_configs_sharing_one_catalog_slug_all_appear():
    """Regression: an earlier catalog-driven implementation silently dropped
    every config stem but one when several configs resolve to the same
    catalog entry (e.g. Qwen-Image-2512/Edit-2509/Edit-2511 all -> qwen-image)."""
    configs = {
        "Qwen-Image": {"model_config": "qwen_image", "collection": {"name": "Qwen-Image"}},
        "Qwen-Image-2512": {"model_config": "qwen_image", "collection": {"name": "Qwen-Image 2512"}},
    }
    catalog = {"qwen-image": DEV_CATALOG["qwen-image"]}

    result = compute_available_models(catalog, configs, {"hf_models": []}, EMPTY_SKIP_RULES)

    assert set(result) == {"Qwen-Image", "Qwen-Image-2512"}
    assert result["Qwen-Image"]["catalog_slug"] == "qwen-image"
    assert result["Qwen-Image-2512"]["catalog_slug"] == "qwen-image"


def test_compute_available_models_config_with_no_catalog_match_still_appears():
    """A genuine catalog gap (config exists, resolve_model_slug finds
    nothing) must not silently vanish from the enumeration."""
    configs = {"Qwen-Image-Layered": {"collection": {"name": "Qwen-Image Layered"}}}

    result = compute_available_models({}, configs, {"hf_models": []}, EMPTY_SKIP_RULES)

    assert result["Qwen-Image-Layered"]["buildable"] is True
    assert result["Qwen-Image-Layered"]["catalog_slug"] is None
    assert result["Qwen-Image-Layered"]["model_family"] is None
    assert result["Qwen-Image-Layered"]["hf_repo_slug"] == "qwen-image-layered"


def test_compute_available_models_skip_rules_all_four_kinds():
    configs = {
        "Flux.1-Dev": {"model_config": "dev", "collection": {"name": "Flux.1 Dev"}},
        "Flux.1-Schnell": {"model_config": "schnell", "collection": {"name": "Flux.1 Schnell"}},
    }
    skip_rules = {
        "families": {"flux1"},  # drops both Flux.1-Dev and Flux.1-Schnell
        "sub_families": set(),
        "models": set(),
        "quants": {},
    }

    result = compute_available_models(DEV_CATALOG, configs, {"hf_models": []}, skip_rules)

    assert "Flux.1-Dev" not in result
    assert "Flux.1-Schnell" not in result
    # unrelated catalog-only entries are unaffected
    assert "boogu-image-turbo" in result


def test_compute_available_models_skip_specific_model_by_slug():
    skip_rules = {**EMPTY_SKIP_RULES, "models": {"boogu-image-turbo"}}
    result = compute_available_models(DEV_CATALOG, {}, {"hf_models": []}, skip_rules)
    assert "boogu-image-turbo" not in result
    assert "qwen-image" in result  # unaffected catalog-only entry


def test_compute_available_models_skip_config_with_no_catalog_slug_by_stem():
    """skipped_models must also accept a config STEM, not just a catalog
    slug -- a config with no catalog match at all (slug is None) has no
    slug to skip by, so the stem is the only handle available. Matched
    case-insensitively, same as load_models_skipped() lowercases it (stems
    are mixed-case, e.g. "Qwen-Image-Layered", but this file is hand-edited
    and drifts on casing)."""
    configs = {"Qwen-Image-Layered": {"collection": {"name": "Qwen-Image Layered"}}}
    skip_rules = {**EMPTY_SKIP_RULES, "models": {"qwen-image-layered"}}

    result = compute_available_models({}, configs, {"hf_models": []}, skip_rules)

    assert "Qwen-Image-Layered" not in result


def test_compute_available_models_skip_specific_quants_for_one_model():
    skip_rules = {**EMPTY_SKIP_RULES, "quants": {"flux2-klein-9b-kv": ["q3"]}}
    result = compute_available_models(DEV_CATALOG, {}, {"hf_models": []}, skip_rules)
    entry = result["flux2-klein-9b-kv"]
    assert "q3" not in entry["quants"]
    assert entry["quants"] == ["q4", "q5", "q6", "q8", "bf16"]
    assert "q3" not in entry["missing_quants"]  # skipped, not counted as missing either


def test_get_available_models_end_to_end(paths):
    _write_json(
        paths["mflux_path"],
        {
            "dev": {
                "model_type": "image",
                "model_family": "flux1",
                "model_sub_family": "flux1-dev",
                "model_aliases": [],
                "upstream": {"repo": "black-forest-labs/FLUX.1-dev"},
                "mflux_cli": [],
                "mflux_cli_tools": [],
            },
            "boogu-image-turbo": {
                "model_type": "image",
                "model_family": "boogu",
                "model_sub_family": "boogu-image-turbo",
                "model_aliases": [],
                "upstream": {"repo": "someone/boogu"},
                "mflux_cli": [],
                "mflux_cli_tools": [],
            },
        },
    )
    _write_json(paths["models_hf_path"], {"hf_models": []})
    skipped_path = paths["mflux_path"].parent / "models_skipped.json"
    _write_json(skipped_path, {"skipped_models": ["boogu-image-turbo"]})

    configs = {
        "Flux.1-Dev": {"model_config": "dev", "collection": {"name": "Flux.1 Dev"}},
    }

    result = get_available_models(configs=configs, skipped_path=skipped_path, **paths)

    assert "Flux.1-Dev" in result
    assert result["Flux.1-Dev"]["buildable"] is True
    assert "boogu-image-turbo" not in result  # excluded by models_skipped.json
