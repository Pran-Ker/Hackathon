# POPFRAME — single-service Railway image.
# Stage 1 builds the Three.js viewer; stage 2 runs the FastAPI pipeline and
# serves the built viewer at /app via pipeline.deploy_app:app.

# ---------- Stage 1: viewer build ----------
FROM node:20 AS viewer
WORKDIR /viewer
COPY viewer/package.json viewer/package-lock.json ./
RUN npm ci
COPY viewer/ ./
RUN npm run build

# ---------- Stage 2: python runtime ----------
FROM python:3.12-slim AS runtime

# ffmpeg for video-frame extraction, libgl/libglib for any cv2/GL consumers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /srv/pipeline
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

# Dependency layer (cached until pyproject/uv.lock change).
COPY pipeline/pyproject.toml pipeline/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Project code. server.py resolves OUTPUTS_DIR relative to this source tree
# (uv installs the root project editable), so outputs live at
# /srv/pipeline/outputs — mount the Railway volume there.
COPY pipeline/pipeline ./pipeline
COPY pipeline/assets ./assets
RUN uv sync --frozen --no-dev

# Committed studio/native HTML: staged as seeds because a volume mounted at
# outputs/ hides the copies baked into the image. deploy_app re-seeds on boot.
COPY pipeline/outputs/imagine/*.html /srv/seed/imagine/

# Built viewer.
COPY --from=viewer /viewer/dist /srv/viewer-dist
ENV VIEWER_DIST=/srv/viewer-dist \
    SEED_DIR=/srv/seed \
    PATH="/srv/pipeline/.venv/bin:$PATH"

EXPOSE 8000
# Shell form so Railway's $PORT expands; default 8000 for local runs.
# --forwarded-allow-ips='*': TLS terminates at the platform edge and the proxy
# hits uvicorn from a non-loopback IP — without this, X-Forwarded-Proto is
# ignored, request.base_url stays http://, and every /files asset URL is
# mixed-content-blocked on the https page.
CMD ["sh", "-c", "uvicorn pipeline.deploy_app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
