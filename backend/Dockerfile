# CareLine backend — public demo deploy (free-tier friendly).
# The deterministic spine + demo console need no API key and no database:
# this image boots keyless, and the eval/prompt artifacts ship inside it so
# the deployed app reports the same version stamps as the repo.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install first for layer caching, then copy the source.
COPY pyproject.toml ./
COPY careline ./careline
COPY prompts ./prompts
COPY policies ./policies
COPY evals ./evals
COPY scripts ./scripts

RUN pip install --no-cache-dir -e ".[api]"

# Non-root runtime user.
RUN useradd --create-home --uid 10001 careline && chown -R careline:careline /app
USER careline

# Budget guards + CORS are set by the deploy config (render.yaml); the app
# stays in development mode so the demo endpoints are mounted, and no secret
# is baked into the image.
EXPOSE 8000

# Gunicorn-free single process keeps the in-memory rate-limit/daily-cap
# counters meaningful (documented README limitation for multi-process).
# --proxy-headers: behind Render's router the socket peer is the router, so
# per-IP limits need the forwarded client IP (single trusted hop).
CMD ["uvicorn", "careline.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
