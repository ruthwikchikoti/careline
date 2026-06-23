# Vinay — Demo Presentation Script (~90 s)

**Segment:** Safety, Evaluation & Observability  
**Audience:** Capstone evaluators

---

## Setup (before recording)

```bash
cd backend
python -m pytest tests/brain/test_bakeoff_safety.py -q   # green
python -m careline.services.demo_runner
```

Have LangSmith open only if `LANGSMITH_API_KEY` is set; offline demo is fine.

---

## Script

> "I own the safety spine — the pre-LLM Triage rails and the five-gate Gatekeeper chain. Uncertainty always resolves toward ESCALATE; gates only downgrade, never upgrade."

**1. Run T1–T8 (15 s)**

> "This is our behavioural oracle — eight scenarios, all green offline."

```bash
python -m pytest tests/brain/test_bakeoff_safety.py -v --tb=no
```

Point at T1 discontinued-med and T6 isolation rows.

**2. Red-flag ESCALATE (20 s)**

> "Chest pain never reaches the LLM — deterministic keyword rail, instant transfer."

Run demo runner; highlight the red-flag block and `TelephonyStub` escalation count.

**3. Gate trace (25 s)**

> "Every verdict is explainable — which gate fired and why."

Show the happy-path trace (scope → risk → cross-condition → confidence → verifier → ANSWER) and one ESCALATE trace (e.g. cross-condition tripwire).

**4. Observability + audit (20 s)**

> "Live path gets LangSmith spans on reasoner and verifier; offline it's a no-op. Every turn is audit-logged — and after DPDP erasure clinical text is nulled but the skeleton stays."

Optionally show audit digest from demo output; mention `redact_patient`.

**5. Close (10 s)**

> "QuestionService is what Naresh's `/internal/run-question` router calls — per-call clarify budget, escalation delivery, and the eval harness the team uses as the correctness oracle."

---

## Refs

- Tasks: VI-1 … VI-8  
- Commit scope: `safety`, `eval`  
- `git log --author="Vinay" --oneline`
