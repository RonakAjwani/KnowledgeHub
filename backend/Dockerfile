# Multi-stage: the dev dependency group (deepeval, pytest, ruff, mypy) is resolved
# but never installed into the runtime image. deepeval alone pulls a large
# transitive tree, and the whole deployment target is a 512 MB box — keeping it
# out of the final layer is what makes the ceiling reachable.

FROM python:3.12-slim AS builder

ENV POETRY_VERSION=2.4.0 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PIP_NO_CACHE_DIR=1

# Poetry goes into its own location, not into the interpreter it manages.
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app

# Copied alone so the dependency layer caches independently of source changes.
COPY pyproject.toml poetry.lock ./

# --only main is the line that keeps the image small; --no-root because this is
# an application, not an installable package (package-mode = false).
RUN poetry install --no-root --only main


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    # 0.1 vCPU: threaded BLAS inside onnxruntime costs more in contention than it
    # buys in throughput, and each extra thread carries its own arena.
    OMP_NUM_THREADS=1 \
    FASTEMBED_CACHE_PATH=/app/.fastembed_cache

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY docker-entrypoint.sh ./docker-entrypoint.sh

# The model is downloaded on first use into a mounted volume rather than baked in,
# so the image stays small and a model swap does not require a rebuild.
RUN mkdir -p /app/.fastembed_cache && chmod +x ./docker-entrypoint.sh

EXPOSE 8000

# The entrypoint runs `alembic upgrade head` before exec'ing into this CMD —
# there is no separate migration step or worker service to run it from, since
# 750 free instance-hours on Render fits exactly one service.
ENTRYPOINT ["./docker-entrypoint.sh"]
# Single worker, deliberately. Two workers on 0.1 vCPU would each load their own
# copy of the ONNX embedding model and exhaust the 512 MB ceiling on their own.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
