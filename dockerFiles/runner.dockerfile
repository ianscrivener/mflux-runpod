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

RUN uv pip install --no-cache pip runpod httpx hf pyyaml
RUN uv pip install --no-cache mlx[cuda13]
RUN uv pip install --no-cache mflux



COPY app/ ./app/
COPY dockerFiles/runner_handler.py ./

# Fail loudly at build time if the venv/deps aren't actually importable --
# a silently-broken image otherwise only surfaces as a worker that starts,
# reports "ready", and never picks up a job (no container logs at all).
RUN python3 -c "import runpod, httpx, hf, yaml; print('handler deps ok')" \
    && uv --version

CMD ["python3", "-u", "runner_handler.py"]
