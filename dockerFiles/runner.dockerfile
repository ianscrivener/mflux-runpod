FROM nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget git openssh-client \
      python3 python3-pip python3-venv \
      libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* 

ENV LD_LIBRARY_PATH="/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cudnn/lib:/opt/venv/lib/python3.12/site-packages/nvidia/nccl/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cufft/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib:${LD_LIBRARY_PATH}"

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mkdir -p /app \
    && cd /app \
    && uv venv venv \
    && source venv/bin/activate \
    && uv init \
    && uv add --upgrade --no-cache pip \
    && uv add --no-cache runpod httpx hf yaml

RUN uv add --no-cache mlx[cuda13]
RUN uv add --no-cache mflux    


WORKDIR /app
COPY app/ ./app/
COPY dockerFiles/runner_handler.py ./

# Fail loudly at build time if the venv/deps aren't actually importable --
# a silently-broken image otherwise only surfaces as a worker that starts,
# reports "ready", and never picks up a job (no container logs at all).
RUN python3 -c "import runpod, httpx, hf, yaml; print('handler deps ok')" \
    && uv --version

CMD ["python3", "-u", "runner_handler.py"]
