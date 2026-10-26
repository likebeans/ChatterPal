FROM python:3.12-slim-bookworm AS builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

# The default retains the full local/UI runtime. For a cloud API use
# --build-arg 'CHATTERPAL_EXTRAS=api cloud'; no Whisper, Torch or PyAudio is installed.
ARG CHATTERPAL_EXTRAS=full
# Build the NLS SDK's crcmod extension in the builder. Only microphone/full
# requires PortAudio headers; neither toolchain is copied to the runtime image.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends build-essential \
    && case " ${CHATTERPAL_EXTRAS} " in \
        *" full "*|*" microphone "*|*" dev "*) \
            apt-get -o Acquire::Retries=3 install -y --no-install-recommends portaudio19-dev ;; \
       esac \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.10.7

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN set --; for extra in ${CHATTERPAL_EXTRAS}; do set -- "$@" --extra "$extra"; done; \
    uv sync --locked --no-dev --no-install-project "$@"
COPY src ./src
RUN set --; for extra in ${CHATTERPAL_EXTRAS}; do set -- "$@" --extra "$extra"; done; \
    uv sync --locked --no-dev --no-editable "$@"


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
    API_HOST=0.0.0.0 \
    API_PORT=8010 \
    LEARNING_DB_PATH=/app/data/learning/learning.sqlite3 \
    GRADIO_ANALYTICS_ENABLED=False

# ffmpeg/flac decode audio; PortAudio and libsndfile support the Python bindings.
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        ca-certificates ffmpeg flac libgomp1 libportaudio2 libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system chatterpal \
    && useradd --system --gid chatterpal --home-dir /app chatterpal

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# Gradio may create flagging directories directly under its working directory.
RUN mkdir -p .cache data/models temp_audio logs .config/chatterpal \
    && chown chatterpal:chatterpal /app \
    && chown -R chatterpal:chatterpal .cache data temp_audio logs .config

USER chatterpal
EXPOSE 8010 7860
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=5)"
CMD ["chatterpal-run", "--mode", "api"]
