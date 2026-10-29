# Basis backend image: the API (default command) and the Temporal worker share one image;
# Cloud Run runs the worker with `python -m basis.worker` and the outbox relay with
# `python -m basis.relay` (docs/deploy/STAGING.md).
#
# Build from backEnd/:  docker build -t basis-backend .
# Run the API:          docker run --rm -e BASIS_ENV=staging ... basis-backend
# Run the worker:       docker run --rm -e BASIS_ENV=staging ... basis-backend python -m basis.worker

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# Dependencies first, from the lockfile only, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Run as a non-root user; nothing is written inside the image at run time.
RUN useradd --create-home --uid 10001 basis

# The embedding model, baked into the image's Hugging Face cache at the revision the code
# loads (basis/retrieval/embed.py: DEFAULT_MODEL and DEFAULT_MODEL_REVISION;
# tests/unit/retrieval/test_embed.py fails when these two ARGs drift from it). Loaded the
# way the worker loads it, so the cache holds exactly the files the load needs (~91 MB).
# A worker then never downloads at start: on a fresh container the first ingest used to
# download and load the model inside the activity, 187 s against a 30 s heartbeat timeout
# (EVAL_REPORT section 39). The worker warms the model before polling and reads the cache
# without a network call; a revision that is not cached is still downloaded, with a
# warning. Before the code is copied, so a code change does not re-download it.
ARG EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
ARG EMBEDDING_MODEL_REVISION=1110a243fdf4706b3f48f1d95db1a4f5529b4d41
ENV HF_HOME=/opt/hf
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('$EMBEDDING_MODEL', revision='$EMBEDDING_MODEL_REVISION', device='cpu')" \
    && chown -R basis:basis /opt/hf

COPY basis ./basis
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Mount point for local storage shared by the API and the worker in docker-compose.yml
# (a named volume copies this directory's owner, so the non-root user can write to it).
RUN mkdir -p /tmp/basis-local-storage && chown basis:basis /tmp/basis-local-storage
USER basis

# The commit the image was built from (CI passes --build-arg GIT_SHA); /health reports it.
# Last, so a new commit changes only this layer's metadata.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

EXPOSE 8000

CMD ["uvicorn", "basis.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
