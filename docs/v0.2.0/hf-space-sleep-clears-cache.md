# Note: Space sleep = full container rebuild, not a pause

When the HF Spaces GPU worker (`cleverheart2026/mflux-model-gpu-runner`)
goes idle and sleeps, it doesn't suspend/resume a running container the way
"sleep" might imply -- it **shuts the container down entirely**. Waking it
back up re-runs the Dockerfile build and starts a fresh container from
scratch, not a resumed one.

## Consequence for the HF Hub cache

`HF_HUB_CACHE` (now `/scriv_data/hf_hub`, per `docker-runner-hf/Dockerfile`)
and `BUILD_ROOT` (`/scriv_data/mflux_models`) both live on that fresh
container's own local disk, created empty by `RUN mkdir -p ...` at image
build time. Since sleep/wake is a full rebuild, not a resume:

- Every sleep→wake cycle clears the HF Hub download cache completely, same
  as an explicit `hf cache rm`/`delete_cache()` would -- just as a side
  effect of the container no longer existing, not because any code in this
  project clears it (see prior discussion: nothing here calls
  `huggingface_hub`'s cache-eviction APIs directly).
- The next build after a wake re-downloads full source weights for whatever
  model series it needs (~20GB+ for some series), even if that exact series
  was already downloaded right before the Space slept.
- This is on top of (not instead of) the existing within-container
  behavior: the cache also just accumulates unbounded across builds *within*
  one continuously-running container, since nothing evicts old entries there
  either -- sleep/wake is simply the one thing that resets it to empty.

## Not yet investigated

- Whether the Space's sleep timeout is long/short enough that this matters
  in practice for the current usage pattern (infrequent manual dispatches
  vs. back-to-back builds).
- Whether `docs/ARCHITECTURE.md`'s existing cache/`BUILD_ROOT` discussion
  (written before the `/scriv_data` move) needs a broader refresh -- it
  still describes the earlier `/data`-based (Persistent Storage) attempt and
  its revert to the *default* HF cache path, not the current explicit
  `/scriv_data` setup. This note doesn't attempt that reconciliation.
