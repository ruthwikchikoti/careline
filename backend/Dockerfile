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

# api = FastAPI/uvicorn/JWT; llm = the OpenAI/Anthropic SDKs, so setting an
# LLM key on the deploy actually reaches the live reasoner instead of failing
# closed to ESCALATE on a missing SDK (REVIEW-7). Both are import-lazy: with no
# key set the image still boots keyless on the deterministic spine.
# TODO(lead): switch to ".[api,llm,obs]" once the llmops package's `obs` extra
# (langfuse) lands in pyproject.toml — it is not defined on this branch yet.
RUN pip install --no-cache-dir -e ".[api,llm]"

# Non-root runtime user.
RUN useradd --create-home --uid 10001 careline && chown -R careline:careline /app
USER careline

# Budget guards + CORS are set by the deploy config (render.yaml); the app
# stays in development mode so the demo endpoints are mounted, and no secret
# is baked into the image.
EXPOSE 8000

# Gunicorn-free single process keeps the in-memory rate-limit/daily-cap and
# login-lockout counters meaningful (documented limitation for multi-process).
# --no-proxy-headers: uvicorn must NOT rewrite the client address from
# X-Forwarded-For — with "--forwarded-allow-ips *" it trusted the leftmost,
# client-controlled entry, so rotating a spoofed XFF bypassed every per-IP
# limit. The app's guards instead key on the rightmost hop appended by the
# trusted proxy (CARELINE_TRUSTED_PROXY_HOPS=1 in render.yaml).
CMD ["uvicorn", "careline.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers"]
