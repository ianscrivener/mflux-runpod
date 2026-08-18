FROM nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget git openssh-client \
      python3 python3-pip python3-venv \
      libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* 

ENV LD_LIBRARY_PATH="/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cudnn/lib:/opt/venv/lib/python3.12/site-packages/nvidia/nccl/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cufft/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib:${LD_LIBRARY_PATH}"

# uv install - Download the latest installer
ADD https://astral.sh/uv/install.sh /uv-installer.sh

# uv install - Run the installer then remove it
RUN sh /uv-installer.sh && rm /uv-installer.sh

# uv install - Ensure the installed binary is on the `PATH`
ENV PATH="/root/.local/bin/:$PATH"

WORKDIR /app

RUN uv venv /opt/venv

RUN uv pip install --no-cache pip runpod httpx huggingface_hub pyyaml boto3

# Baked-in default mlx + mflux -- must match runner_handler.py's
# BAKED_MLX_VERSION / BAKED_MFLUX_TARGET exactly, since the handler only
# reinstalls either of these when a job explicitly requests an override
# (force_mlx_ver / force_mflux_repo). mflux's own pyproject.toml normally
# pins mlx<0.32.0 (works around a known CUDA/Linux quantized_matmul bug in
# mlx>=0.32.0); 0.32.0 is forced here anyway per explicit request, so
# quantized (non-bf16) builds against this default may hit that bug until
# a job passes force_mlx_ver to pin back below 0.32.0.
RUN uv pip install --no-cache "mlx[cuda13]==0.32.0"
RUN uv pip install --no-cache "mflux @ git+https://github.com/mflux-community/mflux.git@main"



COPY app/ ./app/
COPY dockerFiles/runner_handler.py ./

# Fail loudly at build time if the venv/deps aren't actually importable --
# a silently-broken image otherwise only surfaces as a worker that starts,
# reports "ready", and never picks up a job (no container logs at all).
RUN python3 -c "import runpod, httpx, huggingface_hub, yaml, boto3; print('handler deps ok')" \
    && uv --version

CMD ["python3", "-u", "runner_handler.py"]
