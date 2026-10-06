# ============================================================
# Container A — facefusion (Face Swap + Style + Restoration)
# Base: CUDA 12.4 runtime (compatible with onnxruntime-gpu==1.30.0)
#
# Build from SPEED_X_TECHNOLOGY project root:
#   docker build -f docker/facefusion/Dockerfile -t speed_x_technology-facefusion:latest .
# ============================================================

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ARG DEBIAN_FRONTEND=noninteractive

# ── System packages ───────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-dev \
        python3-pip \
        python3.11-venv \
        ffmpeg \
        git \
        libgl1 \
        libglib2.0-0 \
        v4l-utils \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
 && update-alternatives --install /usr/bin/python  python  /usr/bin/python3.11 1

# ── Python deps (pinned per facefusion/requirements.txt) ─────
RUN pip3 install --no-cache-dir \
        gradio-rangeslider==0.0.8 \
        gradio==5.50.0 \
        numpy==2.4.6 \
        onnx==1.23.1 \
        onnxruntime-gpu==1.30.0 \
        opencv-python-headless==5.0.0.93 \
        tqdm==4.70.1 \
        scipy==1.18.1

# ── Application source ────────────────────────────────────────
# Build context is the SPEED_X_TECHNOLOGY project root.
WORKDIR /app
COPY Modules/facefusion/ ./facefusion/
# speed_x_technology/ contains the consent_gate package (Step 2).
# Copied here so it is baked into the image; also mounted as a
# volume in docker-compose for live development iteration.
COPY speed_x_technology/ ./speed_x_technology/

# Shared model weights and SQLite consent DB persist via named volume.
RUN mkdir -p /app/models /app/data

# ── Environment ───────────────────────────────────────────────
# Both the facefusion package and our speed_x_technology package must be on the path.
ENV PYTHONPATH="/app/facefusion:/app"

# UDP stream output port matches streamer.py's hardcoded value:
#   udp://localhost:27000?pkt_size=1316
EXPOSE 27000/udp

# ── Entry point ───────────────────────────────────────────────
# The session orchestrator (Step 3) controls invocation.
# Default CMD prints help for manual testing.
CMD ["python3", "facefusion/facefusion.py", "--help"]
