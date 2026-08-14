# syntax=docker/dockerfile:1
#
# AI Director — local AI video director.
#
# The default build includes every local-model extra (speech / vision /
# embedding / web); the image is large because torch ships CUDA runtime
# libraries. Trim with e.g.:
#
#   docker build --build-arg EXTRAS="--extra web --extra speech" .
#
# GPU use needs the NVIDIA Container Toolkit on the host and `gpus: all`
# in compose — the CUDA userspace comes from the pip wheels, so no
# nvidia/cuda base image is required.

FROM python:3.12-slim

# ffmpeg/ffprobe for all media work; fontconfig + Noto CJK for caption
# rendering (AI Director never assumes drawtext support in ffmpeg).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

ARG EXTRAS="--extra speech --extra vision --extra embedding --extra web"
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer (cached until pyproject/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev ${EXTRAS}

# Project layer
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev ${EXTRAS}

ENV PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/models/hf \
    AIDIRECTOR_CONFIG=/app/config/docker.yaml

VOLUME /models /workspace
EXPOSE 8484

CMD ["aidirector", "web", "--host", "0.0.0.0", "--port", "8484"]
