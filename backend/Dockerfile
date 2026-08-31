# Multi-stage, slim, non-root build (REQUIREMENTS INF-001..INF-013; DECISIONS D-058/D-059).
# Shared by the API and the arq worker: distinct entrypoints, one image (INF-003).

# --- Stage 1: wheels -----------------------------------------------------------
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt requirements-speech.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-speech.txt

# --- Stage 2: runtime ----------------------------------------------------------
FROM python:3.12-slim AS runtime

# libsndfile backs soundfile; ffmpeg decodes browser WebM/Opus recordings
# (the STT ffmpeg fallback, D-079). tesseract + poppler-utils back the forms
# OCR fallback (B-026..B-028) so image-only form pages are extractable.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 ffmpeg tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user (D-058).
RUN groupadd --system --gid 1001 nyaya \
    && useradd --system --uid 1001 --gid nyaya --create-home nyaya

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt requirements-speech.txt ./
RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt \
    && pip install --no-cache-dir --no-index --find-links /wheels -r requirements-speech.txt \
    && rm -rf /wheels

COPY app ./app

# Bake the pinned Piper voices (D-081) so TTS works offline in the container:
# en_US-lessac-medium (default English) + hi_IN-pratham-medium (Hindi).
# piper-tts itself is already installed from requirements-speech.txt wheels.
# Also pre-download the open-weight BGE embedding model (D-011/D-012) into
# the HF cache so dense retrieval needs no network at runtime (A2-001).
ENV HF_HOME=/app/hf-cache
RUN python -m piper.download_voices --download-dir /app/piper-voices \
    en_US-lessac-medium hi_IN-pratham-medium \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-base-en-v1.5')"

# Uploaded PDFs land here (named volume mount point in compose). hf-cache is
# the Hugging Face weights cache (speech models, D-079); it is pre-owned by
# the runtime user so named volumes mount writable on first run.
RUN mkdir -p /app/storage /app/hf-cache && chown -R nyaya:nyaya /app
USER nyaya

ENV STORAGE_DIR=/app/storage

# Liveness via the app health endpoint (INF-011/INF-012). Startup embeds the
# corpus with BGE on CPU (~3-4 min on a small host) before uvicorn accepts
# connections, so the start period must cover that cold start — otherwise
# the container is marked unhealthy and dependency-gated services never start.
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=20 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

EXPOSE 8000

# Default: API server. The worker service overrides the command with
# `arq app.workers.arq_worker.WorkerSettings`.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
