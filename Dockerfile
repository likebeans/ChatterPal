FROM python:3.12-slim-bookworm AS builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

# PyAudio needs PortAudio headers and a compiler; keep them out of the runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential portaudio19-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.10.7

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable


FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    XDG_CACHE_HOME=/app/.cache \
    CACHE_DIR=/app/.cache \
    MODEL_CACHE_DIR=/app/data/models \
    AUDIO_TEMP_DIR=/app/temp_audio \
    TMPDIR=/app/temp_audio \
    GRADIO_ANALYTICS_ENABLED=False

# ffmpeg/flac decode audio; PortAudio and libsndfile support the Python bindings.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates ffmpeg flac libgomp1 libportaudio2 libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system chatterpal \
    && useradd --system --gid chatterpal --home-dir /app chatterpal

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY scripts/run_api.py ./scripts/run_api.py
# Gradio may create flagging directories directly under its working directory.
RUN mkdir -p .cache data/models temp_audio logs .config/chatterpal \
    && chown chatterpal:chatterpal /app \
    && chown -R chatterpal:chatterpal .cache data temp_audio logs .config

USER chatterpal
EXPOSE 8010 7860
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=5)"
CMD ["python", "scripts/run_api.py"]
