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

COPY basis ./basis
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Run as a non-root user; nothing is written inside the image at run time.
RUN useradd --create-home --uid 10001 basis
USER basis

# The commit the image was built from (CI passes --build-arg GIT_SHA); /health reports it.
# Last, so a new commit changes only this layer's metadata.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

EXPOSE 8000

CMD ["uvicorn", "basis.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
