FROM nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget git openssh-client \
      python3 python3-pip python3-venv \
      libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip uv

# mlx[cuda13]'s pip-installed NVIDIA libs (nvidia-cublas, nvidia-cudnn-cu13,
# nvidia-nccl-cu13, nvidia-cufft, nvidia-cuda-nvrtc) land under
# /opt/venv/lib/python3.*/site-packages/nvidia/*/lib -- nothing on PyPI adds
# that to the dynamic linker's search path automatically. Setting
# LD_LIBRARY_PATH here (a real Dockerfile ENV, read once at container start)
# is what a runtime `os.environ["LD_LIBRARY_PATH"] = ...` inside a running
# Flash worker process could NOT do -- glibc's loader caches the search path
# at process startup, so it only ever works when set before the process
# that will do the dlopen()ing actually starts. That's exactly this case.
ENV LD_LIBRARY_PATH="/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cudnn/lib:/opt/venv/lib/python3.12/site-packages/nvidia/nccl/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cufft/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib:${LD_LIBRARY_PATH}"

# mlx/mflux are deliberately NOT installed here. mflux moves fast and this
# project wants to test arbitrary branches/forks (new model support, etc.)
# without rebuilding this image -- the actual
# `uv pip install mlx[cuda13] mflux@git+{repo_url}@{branch}` happens at
# container START (see runner_handler.py::_ensure_mflux_installed), with
# repo_url/branch coming from the job's own input (default:
# mflux-community/mflux@main). `uv` (installed above, on PATH via
# /opt/venv/bin) makes that per-start install fast. This image only carries
# the CUDA/cuDNN runtime + Python + system libs that rarely change.

RUN pip install --no-cache-dir runpod huggingface_hub pyyaml httpx

WORKDIR /app
COPY app/ ./app/
COPY dockerFiles/runner_handler.py ./

CMD ["python3", "runner_handler.py"]
