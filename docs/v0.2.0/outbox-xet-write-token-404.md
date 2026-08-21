# Issue: outbox result delivery fails — 404 on bucket xet-write-token

**Status:** diagnosed, not fixed. Reproduced live 2026-08-21, run #29 (both
q5 and q3 delivery attempts).

## Symptom

After a quant build finishes (successfully or not), the worker tries to
durably deposit the result JSON in the HF bucket outbox so the Orchestrator
can pick it up later (`docker-runner-hf/app/outbox.py:62-72`,
`put_result`). For both quants in run #29, that delivery itself failed:

```
outbox delivery failed -- result for run 29/q5 dropped: {...}
Traceback (most recent call last):
  File "/app/worker.py", line 145, in _worker_loop
    put_result(job["run_id"], job["quant"], payload)
  File "/app/app/outbox.py", line 67, in put_result
    hf.batch_bucket_files(
  ...
ConnectionError: Network error: Request error: HTTP status client error
(404 Not Found), domain:
https://huggingface.co/api/buckets/cleverheart2026/mflux-model-gpu-runner-storage/xet-write-token
```

Same for q3. Both results were dropped — never written to the bucket, so
the Orchestrator's `/outbox/poll` (`app/outbox.py` /
`process_pending()` in `docker-runner-hf/app/outbox.py:109-155`) has nothing
to process for run #29 at all.

## Where it happens

`docker-runner-hf/app/outbox.py:62-72`, `put_result()` →
`huggingface_hub.batch_bucket_files(bucket_id, add=[...], token=...)` →
library internally calls `session.new_upload_commit(...)` which needs to
first fetch a "xet-write-token" from
`/api/buckets/{owner}/{bucket}/xet-write-token` — that endpoint 404s.

This is caught by `worker.py:154-164`'s bare `except Exception`, added
2026-08-20 specifically so a bucket-API hiccup can't kill the worker's
background build-loop thread (per that block's comment, an earlier version
of this exact failure mode took the whole loop down). That fix works as
intended here — the worker stayed alive and picked up nothing worse than a
dropped result — but the result itself is still permanently lost; nothing
retries `put_result`.

## Root cause (most likely)

A 404 on `xet-write-token` for an otherwise-valid, already-in-use bucket
path (`cleverheart2026/mflux-model-gpu-runner-storage`, the same bucket the
worker mounts at `/data` and has apparently written `build/` scratch data to
without issue) suggests one of:

1. **Xet storage isn't enabled for this bucket/account.** HF's bucket
   uploads can be backed by either the legacy LFS-style path or the newer
   "Xet" chunked-storage backend; if the installed `huggingface_hub` version
   defaults to attempting a Xet upload and the bucket/account isn't
   Xet-enabled, the write-token endpoint simply doesn't exist for it →
   404, distinct from an auth failure (401/403).
2. **A `huggingface_hub` version mismatch** between what this Xet-upload
   code path expects and what's actually deployed on `huggingface.co`'s
   buckets API for this account — i.e. client-library-side, not a
   credential problem, since the same `HF_TOKEN` reads/writes other things
   in this bucket fine elsewhere in the project.

Not a permissions issue in the way [[fibo-lite-gated-repo-403]] is — that
one is a clean 403 with an explicit permissions message; this is a 404 on an
API route, which points at "this feature/endpoint isn't available for this
bucket" rather than "you're not allowed."

## Impact

This is the same failure class already called out as a known gap in
`docs/v0.2.0/security-audit-tasks.md` item 5 (dispatch-side silent
run-stuck-at-`running`), but from the opposite end: item 5 is about the
Orchestrator never hearing back because dispatch itself failed; this is the
Orchestrator never hearing back because the *build finished* (or failed) but
the **result report** couldn't be delivered. Both leave a `runs` row stuck
at `running` forever with no error visible in the web UI — from the
operator's side these look identical (a run that never finishes), but the
underlying failures are unrelated and would need separate fixes.

## Not yet investigated

- Confirm the bucket's actual Xet-enablement status via the HF UI/API
  directly (not just inference from the error).
- Check the currently pinned `huggingface_hub` version in
  `docker-runner-hf`'s requirements against what `batch_bucket_files` /
  Xet-upload support requires.
- Whether this bucket write path has ever worked for `results/` specifically
  (vs. `build/`, which the container also writes to but via a different
  mechanism — local mount, not `batch_bucket_files`).
