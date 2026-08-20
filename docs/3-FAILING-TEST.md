# 3 known-failing tests

`.venv/bin/pytest tests/ -q` currently reports **3 failed, 141 passed** on a
clean checkout. All three fail for the same root cause, are pre-existing
(not introduced by any RunPod-removal or HF-worker work on this branch), and
have been repeatedly re-confirmed unchanged across this whole session.

```
FAILED tests/test_generate.py::test_generate_one_uses_dry_run_by_default
FAILED tests/test_generate.py::test_generate_one_force_overwrite_includes_all_quants
FAILED tests/test_main.py::test_report_run_callback_one_quant_of_many_is_partial
```

## Root cause

All three tests hardcode an expected quant list for the `Fibo` series --
`{"q4", "q6", "q8", "bf16"}` -- and call `generate_one("Fibo")` (or
`POST /generate`) without mocking `load_configs()`. `generate_one` calls
`load_configs()` for real, which reads the live
`configs/models/Fibo.yaml` off disk:

```yaml
# configs/models/Fibo.yaml
quants:
  - q3
  - q4
  - q5
  - q6
  - q8
  - bf16
```

That file now declares **six** quants, not the four the tests were written
against -- `q3` and `q5` were added to Fibo's config at some point after
these tests were written, and nothing re-synced the tests. Every assertion
comparing `quants_to_build` against the old four-item list now sees the
real two extra items and fails:

```
AssertionError: assert ['q3', 'q5'] == []
Left contains 2 more items, first extra item: 'q3'
```

This is a **test-isolation gap**, not a code-correctness bug: `generate_one`,
`expected_repo_ids`, and the `/generate` route are all working exactly as
designed. The tests are non-hermetic -- they depend on the live,
uncached state of `configs/models/*.yaml`, so any future edit to
`Fibo.yaml`'s `quants:` list (or any other field these three tests
implicitly depend on) will change their expected result again.

## Failing tests, individually

| Test | File | Fails because |
|---|---|---|
| `test_generate_one_uses_dry_run_by_default` | `tests/test_generate.py` | asserts `set(result["plan"]["quants_to_build"]) == {"q4", "q6", "q8", "bf16"}` |
| `test_generate_one_force_overwrite_includes_all_quants` | `tests/test_generate.py` | asserts the non-overwrite call produces `quants_to_build == []` (four repos stubbed as published) -- fails because two more (`q3`, `q5`) aren't in that stub and so aren't "published" |
| `test_report_run_callback_one_quant_of_many_is_partial` | `tests/test_main.py` | asserts `gen_resp.json()["plan"]["quants_to_build"] == ["q4", "q6", "q8", "bf16"]` |

## Why this hasn't been fixed yet

Fixing it properly means making these tests hermetic -- e.g. monkeypatching
`load_configs()` (or `app.generate.load_configs`) to return a fixed, in-test
config dict instead of reading `configs/models/Fibo.yaml` off disk, the way
most of the rest of the test suite already isolates its inputs (see
`tests/test_generate.py`'s own `isolated_db`/`stub_models_hf` fixtures for
the established pattern). That's a real fix, not a large one, but it's been
explicitly deferred each time it came up this session -- never requested,
and doing it inline would have been scope creep on whatever the actual task
was at the time.

## How to fix (not yet done)

Add a fixture that monkeypatches `app.generate.load_configs` (or
`app.main`'s import path, for the `test_main.py` case) to return a small,
explicit `{"Fibo": {...}}` dict matching whatever quant list the test
actually wants to assert against, instead of relying on the real
`configs/models/Fibo.yaml`. That decouples the test suite from config
content drift entirely -- the same isolation `stub_models_hf` already gives
these tests for the *published* side of the diff, just missing on the
*declared* side.
