"""The offline suite is hermetic even with a developer's backend/.env (claims F3).

``careline.combined`` and ``careline.demo_server`` call ``load_dotenv()`` at
import (the server launch needs it). At v6 importing them mid-collection
filled OPENAI_API_KEY from ``.env``, un-skipping the live smoke test.
tests/conftest.py now blanks the keys and disables dotenv before any import.
"""

from __future__ import annotations

import os

import careline.combined  # noqa: F401  (the import side effect under test)
import careline.demo_server  # noqa: F401
from careline.config import Settings
from tests.conftest import SCRUBBED_KEYS


def test_importing_the_server_entrypoints_loads_no_credentials():
    for key in SCRUBBED_KEYS:
        assert not os.environ.get(key), f"{key} leaked into the test process"
    assert os.environ.get("LANGSMITH_TRACING") == "false"


def test_settings_never_read_backend_dotenv_in_the_suite(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("CARELINE_MONGO_URI=mongodb://from-dotenv:27017\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CARELINE_MONGO_URI", raising=False)
    assert Settings().mongo_uri is None, "backend/.env leaked into Settings()"


def test_load_dotenv_is_inert_in_the_suite(tmp_path, monkeypatch):
    import dotenv

    (tmp_path / ".env").write_text("CARELINE_HERMETIC_PROBE=leaked\n")
    monkeypatch.chdir(tmp_path)
    dotenv.load_dotenv()
    assert "CARELINE_HERMETIC_PROBE" not in os.environ
