# ============================================================
# Container B — voice-changer (Real-time Voice Conversion)
# Base: CUDA 11.8 + cuDNN 8 (required by torch==2.0.1 and
#       onnxruntime-gpu==1.13.1 — note: deliberately different
#       CUDA version from Container A to isolate dependencies)
#
# Build from SPEED_X_TECHNOLOGY project root:
#   docker build -f docker/voice-changer/Dockerfile -t speed_x_technology-voice-changer:latest .
# ============================================================

FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

ARG DEBIAN_FRONTEND=noninteractive

# ── System packages ───────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 \
        python3.10-dev \
        python3-pip \
        python3.10-venv \
        git \
        libsndfile1 \
        libgomp1 \
        espeak \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1 \
 && update-alternatives --install /usr/bin/python  python  /usr/bin/python3.10 1

# ── Python deps (pinned per voice-changer/server/requirements.txt) ──
# torch/torchaudio must be installed from the PyTorch CUDA 11.8 wheel
# index first, before other packages that pin numpy.
RUN pip3 install --no-cache-dir \
        torch==2.0.1+cu118 \
        torchaudio==2.0.2+cu118 \
        --extra-index-url https://download.pytorch.org/whl/cu118

RUN pip3 install --no-cache-dir \
        numpy==1.23.5 \
        scipy==1.10.1 \
        onnxruntime-gpu==1.13.1 \
        uvicorn==0.21.1 \
        pyOpenSSL==23.1.1 \
        python-socketio==5.8.0 \
        fastapi==0.95.1 \
        python-multipart==0.0.6 \
        websockets==11.0.2 \
        resampy==0.4.2 \
        faiss-cpu==1.7.3 \
        torchcrepe==0.0.18 \
        librosa==0.9.1 \
        gin==0.1.6 \
        gin-config==0.5.0 \
        einops==0.6.0 \
        local-attention==1.8.5 \
        sounddevice==0.4.6 \
        dataclasses-json==0.5.7 \
        onnxsim==0.4.28 \
        torchfcpe==0.0.3

# ── VRAM cap — PyTorch memory limiter ────────────────────────
# We cap PyTorch to 2.5 GB (out of the 8 GB budget) so it
# cannot starve facefusion's ONNX models.
# The fraction is set at container startup via the entrypoint
# wrapper below (it must run in-process, not at build time).
#
# The wrapper injects this before importing any torch code:
#   import torch
#   if torch.cuda.is_available():
#       torch.cuda.set_per_process_memory_fraction(0.31)
#       # 0.31 * 8 GB ≈ 2.5 GB — adjust per actual GPU size

# ── Application source ────────────────────────────────────────
WORKDIR /app
COPY Modules/voice-changer/server/ ./server/

# Pretrained model weights mount via named volume.
RUN mkdir -p /app/server/pretrain /app/server/model_dir

# ── VRAM cap entry-point wrapper ──────────────────────────────
# A thin Python shim that sets the torch VRAM fraction before
# launching the real server.  This is the only safe place to
# call set_per_process_memory_fraction — inside the process,
# before any CUDA context is created.
RUN cat > /app/vram_capped_entrypoint.py << 'EOF'
"""
VRAM cap shim for voice-changer container.

Limits PyTorch's CUDA allocator to TORCH_VRAM_FRACTION of the
total GPU memory (default 0.31 → ~2.5 GB on an 8 GB card).
Must be the very first file imported so the cap is applied before
any model weights are loaded.
"""
import os
import sys

# Apply cap before any other torch import
import torch
if torch.cuda.is_available():
    fraction = float(os.environ.get("TORCH_VRAM_FRACTION", "0.31"))
    torch.cuda.set_per_process_memory_fraction(fraction, device=0)
    print(
        f"[speed_x_technology] VRAM cap active: "
        f"{fraction * 100:.0f}% of GPU 0 "
        f"(≈ {fraction * torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB)",
        flush=True,
    )

# Hand off to the real server entry point
sys.path.insert(0, "/app/server")
import runpy
runpy.run_module("MMVCServerSIO", run_name="__main__", alter_sys=True)
EOF

# Expose voice-changer's default HTTP/WebSocket port
EXPOSE 18888

# ── Entry point ───────────────────────────────────────────────
CMD ["python3", "/app/vram_capped_entrypoint.py", \
     "--host", "0.0.0.0", "-p", "18888", "--logLevel", "info"]
