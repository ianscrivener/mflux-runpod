apt update
apt install libgl1 libglib2.0-0 nano -y


echo 'alias uvsrc="source .venv/bin/activate"' >> ~/.bashrc
echo 'alias hfdl="hf download"' >> ~/.bashrc
echo 'alias hfls="hf cache ls"' >> ~/.bashrc

echo 'export HF_XET_HIGH_PERFORMANCE=1' >> ~/.bashrc
echo 'export UV_LINK_MODE=copy' >> ~/.bashrc
echo 'export HF_HOME="/workspace/HF_CACHE"' >> ~/.bashrc

echo 'export HF_TOKEN="xxx"' >> ~/.bashrc

source ~/.bashrc

mkdir -p /workspace/HF_CACHE/hub

cd /workspace/

uv init
source .venv/bin/activate
uv pip install --upgrade "mlx[cuda13]"
uv add -U git+https://github.com/mflux-community/mflux.git
uv add -U hf

git config --global user.name "Ian Scrivener"
git config --global user.email "mflux-community@cleverheart.io"


nvidia-smi

# hf download Qwen/Qwen-Image-Edit-2511

