# mlx-cuda-13 quantized_matmul bug on CUDA/Linux

**Date:** 2026-08-18

## The issue

`mflux` runs on Apple's MLX framework, which added an NVIDIA CUDA backend
(the `mlx-cuda-13` PyPI package) so models can run on non-Apple GPUs.

`mlx>=0.32.0` has a **regression in `quantized_matmul` on the CUDA/Linux
backend** — the matmul kernel used specifically when running quantized
(q4/q6/q8) weights. bf16 builds don't hit it, since they never call that
kernel. On CUDA, `mlx>=0.32.0` + a quantized build means a likely crash or
silently wrong output.

`mflux`'s own `pyproject.toml` normally pins `mlx<0.32.0` for exactly this
reason (confirmed via its own upstream comment).

## Where this bites in this repo

- `dockerFiles/runner_handler.py` bakes `BAKED_MLX_VERSION = "0.32.0"` into
  the Docker runner image **on purpose**, per explicit user request, despite
  this known bug — the baked default may break quantized (non-bf16) builds
  until pinned back down.
- The per-job override `force_mlx_ver` (job input field) lets a single job
  install a different mlx version on top of the baked image — e.g.
  `force_mlx_ver: "0.31.1"` (or any `<0.32.0`) to sidestep the bug for a
  quantized build without rebuilding the image.
- The proven-working live test (Fibo **bf16**) never exercised this bug,
  since bf16 doesn't use `quantized_matmul` at all. The first live q8 test
  is what would actually surface it.

## Takeaway

If a quantized (q4/q6/q8) build on this Docker runner fails or produces
garbage output, and the baked default (mlx==0.32.0) was used, **check
whether the error/output looks like a matmul/quantize/NaN issue before
assuming it's something else** — this is the first suspect. The fix is
`force_mlx_ver` pinned below 0.32.0 for that job, not a code change.
