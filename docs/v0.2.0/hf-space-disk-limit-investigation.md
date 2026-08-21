# Investigation: is the HF cache filling the Space's ephemeral disk?

**Status:** inconclusive -- confirmed the disk cap and current hardware
tier, but couldn't get real disk-usage numbers (SSH command execution was
blocked). Not a diagnosed bug, just what's known so far. 2026-08-21.

## The suspicion

The worker container might be resetting/restarting because
`HF_HUB_CACHE`/`BUILD_ROOT` (`/scriv_data`, see
`docs/v0.2.0/hf-space-sleep-clears-cache.md`) grows large enough to hit the
Space's ephemeral disk limit, and HF evicts the container as a result.

## What's confirmed

- **Current hardware**: `cleverheart2026/mflux-model-gpu-runner` is running
  on `l40sx1` (`hf spaces info cleverheart2026/mflux-model-gpu-runner
  --expand runtime`, checked live 2026-08-21: `"hardware": "l40sx1"`,
  `"stage": "RUNNING"`, `"sleep_time": 300`).
- **Disk cap for that tier**: per HF's official hardware spec table
  ([Using GPU Spaces](https://huggingface.co/docs/hub/en/spaces-gpus)),
  `1x Nvidia L40S` gets **380 GB** of ephemeral disk. (Other tiers, for
  reference: CPU Basic/Upgrade 50 GB, T4-small 50 GB, T4-medium 100 GB,
  A10G-small 110 GB, A10G-large 200 GB, A100-large 1000 GB.)
- **What happens at the cap**: per HF's docs and multiple forum
  reports/write-ups, when a Space's root ephemeral disk fills, the platform
  evicts the running container with a specific, identifiable error --
  `"Workload evicted, storage limit exceeded (<N>G)"` (the `<N>` matching
  the tier's cap, e.g. `50G`, `100G`, `200G`). This is a distinct failure
  mode from a build error or an app crash.
- **`sleep_time` is 300 seconds** on this Space (confirmed in the same
  `runtime` payload, `"gcTimeout": 300`) -- any 5-minute idle gap between
  builds already triggers a full container rebuild (per
  `hf-space-sleep-clears-cache.md`), which wipes `/scriv_data` back to
  empty regardless of disk pressure. This works against the cache ever
  accumulating close to 380 GB under normal, spaced-out usage.
- **No eviction message seen yet.** The only container log reviewed so far
  (Fibo-lite run #29, see `fibo-lite-gated-repo-403.md` and
  `outbox-xet-write-token-404.md`) shows a 403 on a gated repo and a 404 on
  the outbox bucket's `xet-write-token` endpoint -- neither is a
  disk-eviction message.

## What's NOT confirmed

- **Actual current disk usage on `/scriv_data`.** Attempted to check via
  `hf spaces ssh cleverheart2026/mflux-model-gpu-runner` (Dev Mode is on
  for this Space) and run `df -h /scriv_data`, but the SSH command
  execution was blocked by the local environment's permission classifier
  (treated as an opaque remote action) before it could run. No real
  disk-usage numbers were obtained.
- Whether the container restarts the user has observed actually coincide
  with high disk usage, vs. one of the two already-documented failure modes
  ([[fibo-lite-gated-repo-403]], [[outbox-xet-write-token-404]]) or the
  routine 300s sleep cycle, which also looks like a "reset" from the
  outside even though it's not disk-related.

## Working conclusion

**Unlikely as the primary cause, but not ruled out.** 380 GB is a lot of
headroom against the Dockerfile's own ~20GB+-per-series download estimate,
and the 300s sleep timeout already resets the cache far more often than
disk growth alone would require -- it becomes a real risk only if many
distinct model series get dispatched back-to-back with under-5-minute gaps
for long enough to outrun that reset cycle.

## Next step to actually settle this

Run `df -h /scriv_data` (or `du -sh /scriv_data/*` for a breakdown) inside
the container to get real numbers, and separately check whether the
container's own logs (via `hf spaces logs cleverheart2026/mflux-model-gpu-runner`
or the Space's logs UI) ever show the `"Workload evicted, storage limit
exceeded"` message specifically. Either of those would turn this from a
suspicion into a confirmed answer.
