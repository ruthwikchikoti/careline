"""Vercel entrypoint — a module-level ASGI ``app`` for the Python runtime.

Vercel's Python runtime imports ``app`` from the module named in
``[tool.vercel] entrypoint`` (``pyproject.toml``); ``create_app`` is a factory,
so this module calls it once. Configuration comes only from the project's
environment variables (no ``.env`` is uploaded: see ``.vercelignore``).

Owner: Ruthwik (scope ``repo``).
"""

from __future__ import annotations

from careline.api.app import create_app

app = create_app()

__all__ = ["app"]
