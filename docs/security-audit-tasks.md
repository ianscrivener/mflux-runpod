# Security Audit Tasks

1. ✅ Review and reconsider architecture for `data-hf-sync/models_queue.json` ~ `https://huggingface.co/buckets/mflux-community/ci/resolve/models_queue.json?download=true`. Decided: DO Spaces (`app/queue_store.py`) is the real master; the HF bucket copy is a throwaway mirror, refreshed via `push("models_queue")`.
2. Verify the DO Spaces bucket's read/write ACLs actually match "only this app writes the queue master" (not yet checked).
3. Scan `data-hf-sync/models_queue.json`'s actual content, once real (non-placeholder) data exists, for secrets/PII before treating the HF mirror as safe to leave public.
4. Decide whether the HF mirror should be removed/sanitized before any wider release, given task 3's outcome.

