"""Session-wide hermetic environment for the offline suite (claims F3).

``python -m pytest -q`` from ``backend/`` must be green and keyless even when
a developer's ``backend/.env`` holds live credentials. Two server entrypoints
(``careline.combined``, ``careline.demo_server``) call ``load_dotenv()`` at
import so that ``uvicorn careline.combined:app --factory`` sees
``LANGSMITH_API_KEY`` before the tracing adapter reads it — and the suite DOES
import them (``tests/api/test_console_audit_bridge.py``; ``create_app`` mounts
the demo routes from ``careline.combined``). At v6 that import filled
``OPENAI_API_KEY`` from ``.env`` mid-collection, so the live smoke test
un-skipped and a stale key failed the run.

This conftest is loaded before any test module is imported, and:

1. sets every provider / tracing key to an EMPTY value — present-but-empty,
   so ``load_dotenv()`` (``override=False``) can never fill it, and every
   "is a key configured?" check reads it as absent — and removes
   ``CARELINE_MONGO_URI`` (unset, not empty, as CLAUDE.md asks);
2. turns ``dotenv.load_dotenv`` into a no-op for the session, so no other
   ``.env`` key reaches ``os.environ`` through an import side effect;
3. stops pydantic-settings reading ``backend/.env`` for ``Settings()``.

A test that needs a key or a CARELINE_* value sets it itself with
``monkeypatch.setenv`` (which still works). Values are never printed.
"""

from __future__ import annotations

import os

#: Keys that would make the suite reach a live service. Blank, not deleted.
BLANKED_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_API_KEY",
    "CARELINE_LANGFUSE_PUBLIC_KEY",
    "CARELINE_LANGFUSE_SECRET_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)
#: Removed outright (an empty Mongo URI is not the same as no Mongo URI).
REMOVED_KEYS: tuple[str, ...] = ("CARELINE_MONGO_URI",)
SCRUBBED_KEYS: tuple[str, ...] = BLANKED_KEYS + REMOVED_KEYS

for _key in BLANKED_KEYS:
    os.environ[_key] = ""
for _key in REMOVED_KEYS:
    os.environ.pop(_key, None)
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

try:  # python-dotenv is optional (the `llm` extra)
    import dotenv
    import dotenv.main

    def _no_dotenv(*_args, **_kwargs) -> bool:
        return False

    dotenv.load_dotenv = _no_dotenv
    dotenv.main.load_dotenv = _no_dotenv
except ImportError:  # pragma: no cover - dotenv not installed
    pass

from careline.config import Settings  # noqa: E402  (after the scrub, on purpose)

Settings.model_config["env_file"] = None
