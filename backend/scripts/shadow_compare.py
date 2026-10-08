"""Shadow comparison — the offline stand-in for a canary deploy.

Runs the same 250 eval questions through two **red-flag policy versions** *in
the same process* and prints a side-by-side metrics table, so a candidate
release can be judged on evidence before it merges. Chosen over a live canary
because the traffic here is fictional and low-volume, and emergency recall —
the metric that matters — is too rare in organic traffic for a canary to
measure at all.

Each arm's emergency rail is rebuilt **from its versioned policy artifact**
(``policies/red-flags.<v>.yaml``), not from today's regex constants — so the
"v1 (regex only)" arm really is v1:

* ``patterns`` only (v1) → literal regex rail; acute-concern net off. Exact
  for the rail.
* ``patterns`` + ``semantic`` (v2) → literal + semantic danger-phrase layer
  with that artifact's phrases and threshold, scored by today's scorer
  (the scoring code has not changed since v2); acute-concern net off.
* the **active** manifest version → the live domain rails, unpatched (exact).
* a non-active version with a ``context`` section (v3+) → APPROXIMATE: the
  history/denial suppression and the acute-concern net are code, not data,
  and cannot be rebuilt from the artifact alone. The table says so.

Reproduction scope — stated in every report: only the *rail* is swapped;
every other component (gates, retrieval, keyless reasoner, eval labels) is
today's code. Measured: the v1 arm reproduces baseline-v0's 58/60 missed
emergencies exactly; its in-scope accuracy / over-escalation differ because
the gates and retrieval improved since. To reproduce a historical report
byte-for-byte, replay the gate on the tree of the commit that produced it —
``--replay <commit>`` does this read-only (``git archive`` into a temp dir,
run that commit's own eval gate); the committed ``baseline-v0`` came from
``b8476c9`` and replays exactly. Manual equivalent::

    git worktree add /tmp/careline-v0 b8476c9
    cd /tmp/careline-v0/backend && python -m careline.services.eval_gate

Usage::

    cd backend && python -m scripts.shadow_compare                 # v1 vs active
    python -m scripts.shadow_compare --a v1 --b v3 --markdown evals/reports/shadow-v1-vs-v3.md
    python -m scripts.shadow_compare --a v2 --b v4 --json out.json
    python -m scripts.shadow_compare --replay b8476c9              # exact historical replay

Owner: Naresh (scope ``services``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

import careline.domain.brain.brain as brain_module
import careline.domain.gates.chain as chain_module
from careline.adapters.llm.prompt_registry import _manifest, load_policy
from careline.domain.rails import red_flag
from careline.services.eval_gate import _load_seed, load_cases, run_keyless, score

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIR = _BACKEND_ROOT / "policies"
BASELINE_V0_COMMIT = "b8476c9"


@dataclass(frozen=True)
class PolicyRail:
    version: str
    sha256_12: str
    check_red_flag: Callable[[str], str | None] | None  # None = live domain rail
    check_acute_concern: Callable[[str], str | None] | None  # None = live net
    fidelity: str


def available_versions() -> list[str]:
    found = []
    for p in _POLICY_DIR.glob("red-flags.v*.yaml"):
        m = re.fullmatch(r"red-flags\.(v\d+)\.yaml", p.name)
        if m:
            found.append(m.group(1))
    return sorted(found, key=lambda v: int(v[1:]))


def active_version() -> str:
    return _manifest()["policies"]["red_flags"]["version"]


def _semantic_checker(semantic: dict) -> Callable[[str], str | None]:
    threshold = float(semantic["threshold"])
    vectors = tuple(
        (concept, red_flag._content_tokens(red_flag._normalize(p)),
         red_flag._trigrams(red_flag._normalize(p)))
        for concept, phrases in semantic["phrases"].items()
        for p in phrases
    )

    def check(question: str) -> str | None:
        norm = red_flag._normalize(question)
        q_tokens = red_flag._content_tokens(norm)
        q_tri = red_flag._trigrams(norm)
        best_concept, best = None, 0.0
        for concept, p_tokens, p_tri in vectors:
            if not p_tokens:
                continue
            sim = 0.6 * (len(q_tokens & p_tokens) / len(p_tokens)) + 0.4 * red_flag._cosine(
                q_tri, p_tri
            )
            if sim > best:
                best_concept, best = concept, sim
        return f"semantic:{best_concept}" if best >= threshold else None

    return check


def build_rail(version: str) -> PolicyRail:
    """Rebuild the emergency rail for ``version`` from its policy artifact."""
    path = _POLICY_DIR / f"red-flags.{version}.yaml"
    if not path.is_file():
        raise SystemExit(f"no policy artifact for {version}: {path} (have {available_versions()})")
    text = path.read_text(encoding="utf-8")
    sha12 = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    policy = yaml.safe_load(text)

    if version == active_version():
        # load_policy() verifies the manifest hash — the live rail IS this artifact.
        load_policy("red_flags")
        return PolicyRail(version, sha12, None, None, "exact — active policy, live domain rails")

    literal = re.compile("|".join(f"(?:{p})" for p in policy["patterns"]), re.IGNORECASE)
    semantic = _semantic_checker(policy["semantic"]) if policy.get("semantic") else None

    def check(question: str) -> str | None:
        if not question:
            return None
        m = literal.search(question)
        if m:
            return m.group(0)
        return semantic(question) if semantic is not None else None

    if policy.get("context"):
        fidelity = (
            "APPROXIMATE — this version's context layer (history/denial suppression, "
            "acute-concern net) is code, not artifact data; it is OFF in this arm. "
            "Use the git-worktree approach for an exact replay."
        )
    elif semantic is not None:
        fidelity = "rail rebuilt from artifact (literal + semantic, this version's phrases/threshold)"
    else:
        fidelity = "rail rebuilt from artifact (literal patterns only)"
    return PolicyRail(version, sha12, check, lambda _q: None, fidelity)


def run_variant(rail: PolicyRail) -> dict:
    # The rail is imported in TWO places (Brain's pre-LLM rail and the scope
    # gate's defense-in-depth re-check) and the v3 acute net lives in the
    # gate — a faithful shadow patches all three.
    originals = (
        brain_module.check_red_flag,
        chain_module.check_red_flag,
        chain_module.check_acute_concern,
    )
    try:
        if rail.check_red_flag is not None:
            brain_module.check_red_flag = rail.check_red_flag
            chain_module.check_red_flag = rail.check_red_flag
        if rail.check_acute_concern is not None:
            chain_module.check_acute_concern = rail.check_acute_concern
        patients, now = _load_seed()
        metrics = score(run_keyless(load_cases(), patients, now))
    finally:
        (
            brain_module.check_red_flag,
            chain_module.check_red_flag,
            chain_module.check_acute_concern,
        ) = originals
    metrics["policy"] = f"red_flags@{rail.version}+{rail.sha256_12}"
    metrics["fidelity"] = rail.fidelity
    return metrics


_METRIC_ROWS = [
    ("Emergency recall", "emergency_recall", "higher_better", 3),
    ("Missed emergencies", "missed_emergencies", "lower_better", 0),
    ("Cross-patient leaks", "cross_patient_leaks", "lower_better", 0),
    ("Superseded leaks", "superseded_leaks", "lower_better", 0),
    ("Injection answered", "injection_answered", "lower_better", 0),
    ("Ungrounded answers", "ungrounded_answers", "lower_better", 0),
    ("Out-of-scope redirect acc.", "out_of_scope_redirect_accuracy", "higher_better", 3),
    ("No-answer accuracy", "no_answer_accuracy", "higher_better", 3),
    ("In-scope answer acc. (keyless)", "in_scope_answer_accuracy", "higher_better", 3),
    ("Over-escalation rate", "over_escalation_rate", "lower_better", 3),
    ("Latency p50 (ms)", "latency_ms_p50", "lower_better", 3),
    ("Latency p99 (ms)", "latency_ms_p99", "lower_better", 3),
]


def render(a: dict, b: dict, a_label: str, b_label: str) -> str:
    n = a.get("n", 250)
    lines = [
        "# Shadow comparison — candidate release vs incumbent",
        "",
        f"*When:* {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"*Set:* the full {n}-item eval set, keyless deterministic slice",
        f"*A:* `{a['policy']}` — {a['fidelity']}",
        f"*B:* `{b['policy']}` — {b['fidelity']}",
        "",
        "| Metric | A: " + a_label + " | B: " + b_label + " | Better |",
        "|---|---|---|---|",
    ]
    for title, key, direction, nd in _METRIC_ROWS:
        va, vb = a.get(key), b.get(key)
        if va is None or vb is None:
            continue
        fa = f"{va:.{nd}f}" if isinstance(va, float) else str(va)
        fb = f"{vb:.{nd}f}" if isinstance(vb, float) else str(vb)
        if va == vb:
            better = "="
        elif (vb > va) if direction == "higher_better" else (vb < va):
            better = "**B**"
        else:
            better = "A"
        lines.append(f"| {title} | {fa} | {fb} | {better} |")
    lines += [
        "",
        "**Reproduction scope.** Only the emergency rail differs between arms; it is "
        "rebuilt from each version's policy artifact. Gates, retrieval, the keyless "
        "reasoner and the eval labels are today's code, so a historical report is "
        "reproduced only as far as those are unchanged. For a byte-for-byte replay "
        f"run the gate in a git worktree at the producing commit (baseline-v0: "
        f"`git worktree add /tmp/careline-v0 {BASELINE_V0_COMMIT}`).",
        "",
        "Verdict: promote B iff every safety metric is B-or-equal and the",
        "eval gate passes on B — see evals/reports/ for the gate run.",
    ]
    return "\n".join(lines) + "\n"


def replay_commit(commit: str) -> dict:
    """Run ``commit``'s own eval gate on its own tree (read-only; no checkout).

    ``git archive <commit> backend`` → temp dir → ``python -m
    careline.services.eval_gate --json`` there with that tree on PYTHONPATH.
    Returns its metrics JSON (the gate's exit code is reported, not raised).
    """
    import io
    import os
    import subprocess
    import sys
    import tarfile
    import tempfile

    repo_root = _BACKEND_ROOT.parent
    archive = subprocess.run(
        ["git", "archive", commit, "backend"], cwd=repo_root, check=True, capture_output=True
    ).stdout
    with tempfile.TemporaryDirectory(prefix=f"careline-{commit}-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter="data")
        backend = Path(tmp) / "backend"
        out = Path(tmp) / "replay.json"
        env = {**os.environ, "PYTHONPATH": str(backend)}
        proc = subprocess.run(
            [sys.executable, "-m", "careline.services.eval_gate", "--json", str(out)],
            cwd=backend, env=env, capture_output=True, text=True,
        )
        if not out.is_file():
            raise SystemExit(f"replay of {commit} produced no metrics:\n{proc.stderr[-2000:]}")
        metrics = json.loads(out.read_text(encoding="utf-8"))
    metrics["replayed_commit"] = commit
    metrics["replay_gate_exit_code"] = proc.returncode
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replay", metavar="COMMIT",
        help="exact historical replay: run COMMIT's own eval gate on its own tree",
    )
    parser.add_argument("--a", default="v1", help="incumbent policy version (default v1)")
    parser.add_argument("--b", default=None, help="candidate policy version (default: active)")
    parser.add_argument("--a-label", default=None)
    parser.add_argument("--b-label", default=None)
    parser.add_argument("--markdown", help="write the comparison table here")
    parser.add_argument("--json", dest="json_out", help="write both arms' metrics here")
    args = parser.parse_args(argv)

    if args.replay:
        metrics = replay_commit(args.replay)
        print(json.dumps(metrics, indent=2))
        if args.json_out:
            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_out).write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
        return 0

    b_version = args.b or active_version()
    a = run_variant(build_rail(args.a))
    b = run_variant(build_rail(b_version))
    a_label = args.a_label or f"red_flags@{args.a}"
    b_label = args.b_label or f"red_flags@{b_version}"
    table = render(a, b, a_label, b_label)
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(table, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps({"a": a, "b": b}, indent=2) + "\n",
                                       encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
