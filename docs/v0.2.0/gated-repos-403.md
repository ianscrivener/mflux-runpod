# Issue: Throws error 403 on gated repo download

**Status:** diagnosed, not fixed. Reproduced live 2026-08-21, run #29 (Fibo-lite q5 + q3, both quants).

## Symptom

Both quant builds for run #29 failed immediately during model init, before any
quantization/upload work started:

```
Downloading model from HuggingFace: briaai/Fibo-lite...
build failed: Fibo-lite/q5
httpx.HTTPStatusError: Client error '403 Forbidden' for url
'https://huggingface.co/briaai/Fibo-lite/resolve/<commit>/text_encoder/config.json'

huggingface_hub.errors.HfHubHTTPError: ...
403 Forbidden: Please enable access to public gated repositories in your
fine-grained token settings to view this repository..
Cannot access content at: https://huggingface.co/briaai/Fibo-lite/resolve/<commit>/text_encoder/config.json.
Make sure your token has the correct permissions.
```

Same failure for both q5 and q3 — not quant-specific.

## Where it happens

`docker-runner-hf/app/runner.py:105` → `mflux`'s `Fibo.__init__` →
`FIBOInitializer.init` → `WeightLoader.load` → `PathResolution._execute` →
`huggingface_hub.snapshot_download(repo_id="briaai/Fibo-lite", ...)`
(`.venv/lib/python3.11/site-packages/mflux/models/fibo/fibo_initializer.py:26-39`,
`.../mflux/models/common/resolution/path_resolution.py:84`).

`runner.py` never passes a token explicitly to this call —
`huggingface_hub` picks up `HF_TOKEN` from the container environment
implicitly.

## Root cause (most likely)

The HF error message is explicit: *"Please enable access to public gated
repositories in your fine-grained token settings"*. `briaai/Fibo-lite` is a
gated repo on the Hub. The worker's `HF_TOKEN` is (per
`docker-runner-hf/app/outbox.py:14-24`) a fine-grained personal access token
scoped for this project's other needs (private Space auth, bucket
read/write). Fine-grained tokens have a separate, opt-in permission for
*"Read access to contents of all public gated repos you can access"* — if
that box isn't checked on the token, every gated-repo download 403s exactly
like this, independent of whether the repo's own access request has been
approved.

Two things need to be true for this to work, and it's unclear yet which (if
either) currently holds:
1. The account owning `HF_TOKEN` (`cleverheart2026`) must have an
   **approved** access request on `briaai/Fibo-lite`'s own gate.
2. The token itself must have the **"public gated repos" fine-grained
   permission** enabled.

Not yet checked which of these is missing — could be one or both.

## Not yet investigated

- Whether `cleverheart2026` has requested/been granted access to
  `briaai/Fibo-lite` on the Hub UI.
- Whether the current `HF_TOKEN`'s fine-grained scope includes gated-repo
  read access.
- Whether this affects only `Fibo-lite` (repo-specific gate) or every gated
  model this project might build (systemic token config issue).

## Related

This failure surfaces as a build failure, but per
[[outbox-xet-write-token-404]] the failure *report itself* also failed to
deliver back to the Orchestrator for this same run — so run #29 shows no
visible error in the web UI at all, just stuck at `running`. That's a
separate bug, documented separately.
