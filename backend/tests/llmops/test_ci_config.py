"""CI / packaging contract — the pipeline gates what the docs say it gates.

* the eval-gate job compares against the NEWEST accepted baseline
  (after-policy-v8.json) — on the shared case ids, which needs per_case;
* the optional LLM slice never looks like a silent green when skipped;
* the dev extra installs mongomock-motor, so tests/data actually runs in CI
  instead of being skipped at import.

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import tomllib

from careline.services import eval_gate

_BACKEND = eval_gate._BACKEND_ROOT
_CI = (_BACKEND.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _job(name: str) -> str:
    start = _CI.index(f"\n  {name}:")
    rest = _CI[start + 1:]
    nxt = [i for i in (rest.find("\n  llm-eval:"), rest.find("\n  eval-gate:"),
                       rest.find("\n  tests:")) if i > 0]
    return rest[: min(nxt)] if nxt else rest


def test_eval_gate_job_uses_the_newest_accepted_baseline():
    job = _job("eval-gate")
    assert "--baseline evals/reports/after-policy-v8.json" in job
    assert "after-policy-v3.json" not in job
    assert "after-policy-v4.json" not in job
    assert "after-policy-v5.json" not in job
    assert "after-policy-v6.json" not in job
    assert "after-policy-v7.json" not in job


def test_newest_accepted_baseline_exists():
    assert (_BACKEND / "evals" / "reports" / "after-policy-v8.json").is_file()


def test_llm_slice_skip_is_written_to_the_job_summary():
    job = _job("llm-eval")
    assert '"$code" -eq 2' in job
    assert "SKIPPED (no OPENAI_API_KEY secret)" in job
    assert "$GITHUB_STEP_SUMMARY" in job


def test_dev_extra_installs_mongomock_motor():
    pyproject = tomllib.loads((_BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    dev = pyproject["project"]["optional-dependencies"]["dev"]
    assert any(dep.replace("_", "-").startswith("mongomock-motor") for dep in dev)
