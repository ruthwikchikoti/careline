"""Score a blind emergency battery against the active red-flag policy.

A blind battery is written by an evaluator that has never read the rails'
code, *before* it is scored, and is never used to tune the release it
measures. Once a release is tuned on a battery, that battery becomes dev data
and the next release needs a fresh one — so each battery measures exactly one
policy version (see ``evals/blind/README.md``).

Runs the keyless Brain (heuristic twins) for the demo patient at the seed's
reference ``now`` and reports recall, false-escalation rate, and the worst
case — an emergency that ended in ANSWER.

Usage::

    python -m scripts.score_blind evals/blind/battery-2.json
    python -m scripts.score_blind evals/blind/battery-2.json --json out.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from careline.adapters.llm.heuristic import HeuristicReasoner, HeuristicVerifier
from careline.adapters.llm.prompt_registry import active_versions
from careline.domain.brain.brain import Brain
from careline.domain.enums import Verdict
from careline.domain.model.call_session import CallSession
from careline.services.eval_gate import _load_seed

PATIENT_ID = "ravi-kumar"


def score_battery(battery: dict) -> dict:
    patients, now = _load_seed()
    patient = patients[PATIENT_ID]
    brain = Brain(reasoner=HeuristicReasoner(), verifier=HeuristicVerifier())
    out: dict = {"policy": active_versions()["red_flags"]}
    for split in ("emergencies", "benign"):
        rows = []
        for i, question in enumerate(battery[split]):
            session = CallSession(
                call_id=f"blind-{split}-{i}",
                patient_id=PATIENT_ID,
                doctor_id=patient.doctor_id,
                max_clarify_turns=2,
            )
            decision = brain.run_question(
                question=question, patient=patient, now=now, session=session
            )
            rows.append({"text": question, "verdict": decision.verdict.value,
                         "scope": decision.scope.value if decision.scope else None})
        out[split] = rows
    emergencies, benign = out["emergencies"], out["benign"]
    out["recall"] = sum(r["verdict"] == Verdict.ESCALATE.value for r in emergencies) / len(emergencies)
    out["false_escalation_rate"] = (
        sum(r["verdict"] == Verdict.ESCALATE.value for r in benign) / len(benign)
    )
    out["answered_emergencies"] = sum(r["verdict"] == Verdict.ANSWER.value for r in emergencies)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("battery", help="blind battery JSON: {emergencies: [...], benign: [...]}")
    parser.add_argument("--json", dest="json_out", help="write per-message results here")
    args = parser.parse_args(argv)
    result = score_battery(json.loads(Path(args.battery).read_text(encoding="utf-8")))
    n_e, n_b = len(result["emergencies"]), len(result["benign"])
    print(f"policy {result['policy']}")
    print(f"recall {round(result['recall'] * n_e)}/{n_e} ({result['recall']:.1%})")
    print(f"false escalation {round(result['false_escalation_rate'] * n_b)}/{n_b} "
          f"({result['false_escalation_rate']:.1%})")
    print(f"emergencies answered (worst case) {result['answered_emergencies']}")
    for r in result["emergencies"]:
        if r["verdict"] != Verdict.ESCALATE.value:
            print(f"  MISS  [{r['verdict']}] {r['text']}")
    for r in result["benign"]:
        if r["verdict"] == Verdict.ESCALATE.value:
            print(f"  FALSE [{r['scope']}] {r['text']}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
