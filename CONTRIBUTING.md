# Contributing: CareLine Ops

The working agreements every commit follows. This is a 6-member team.

## 1. Areas and owned paths

Paths are under `backend/` unless stated. The table describes areas of
ownership. It is not an authorship record.

| Member | Area | Owned paths |
|---|---|---|
| **Bhargav** | *to be confirmed by the team* | n/a |
| **Chikoti Ruthwik** | orchestration (`graph`, `brain`, `repo`) | `careline/adapters/orchestration/`, `careline/domain/brain/`, `careline/domain/model/decision.py`, `careline/domain/enums.py`; repo scaffold and top-level docs |
| **Naga** | data (`data`) | `careline/adapters/mongo/`, `careline/adapters/memory/`, `careline/domain/model/{fact,temporal,patient,consent,consultation}.py`, `careline/domain/ports/{memory,repositories}.py` |
| **Naresh** | API and LLMOps services (`api`, `services`) | `careline/api/`, `careline/adapters/auth/`, `careline/config.py`, `careline/services/*_service.py`, `careline/services/{eval_gate,llm_eval,online_monitor}.py`, `careline/adapters/observability/`, `scripts/`, `.github/workflows/`, `render.yaml`, `Dockerfile` |
| **Srujan** | LLM adapters (`llm`) | `careline/adapters/llm/`, `careline/adapters/factory.py`, `careline/domain/ports/reasoning.py`, `careline/domain/model/proposal.py`, `prompts/` |
| **Priyanshu** | safety and eval (`safety`, `eval`) | `careline/domain/rails/`, `careline/domain/gates/`, `careline/domain/scoring/`, `careline/domain/thresholds.py`, `careline/domain/model/call_session.py`, `careline/adapters/telephony/`, `policies/`, `evals/` |

Stage only files inside your area. A shared file (`CLAUDE.md`, the DI wiring in
`api/app.py`, `adapters/factory.py`, `prompts/manifest.yaml`) is edited by its
owner after a heads-up to the team.

## 2. Commit convention
```
<type>(<scope>): <imperative summary ≤ 72 chars>

<why this change; what it enables; any trade-off; before → after numbers for gated changes>

Refs: <TASK-ID>
```
- `type` is one of `feat | fix | test | docs | refactor | chore`.
- `scope` is your area's scope.
- For safety-critical work (rails, gates, brain), commit the **failing test
  first** as its own commit, then the fix.
- Commit under your own git identity.

## 3. Green before every commit
```bash
cd backend
python -m pytest -q                       # offline, keyless
python -m careline.services.eval_gate     # keyless gate, must PASS
```
Never push red to the shared branch.

## 4. Shipping a policy or prompt release
1. Write the failing probes (test-first commit).
2. Change the rails or the prompt, and add the new `policies/red-flags.vN.yaml`
   or `prompts/<name>/vN.md`. Never edit a released version in place.
3. Re-hash and pin it in `prompts/manifest.yaml`, with a changelog note that gives
   the measured before and after numbers.
4. Run the gate against the previous accepted report:
   `python -m careline.services.eval_gate --baseline evals/reports/after-policy-v<N-1>.json --json evals/reports/after-policy-vN.json --markdown evals/reports/after-policy-vN.md`.
5. Point `.github/workflows/ci.yml` at the new report. A second member reviews
   any baseline or label change before merge.
6. After merge, tag the release (`git tag -a release/red-flags-vN <commit>` and
   `git push origin --tags`).

Eval items added to fix a failure are **dev data** (`held_out=false`, with a
note saying why). Generalisation is measured only on a blind battery written
before the fix (`backend/evals/blind/README.md`).

## 5. The one rule the whole codebase serves
> **Uncertainty always resolves toward ESCALATE. Never answer from a superseded
> fact. One patient per call, with zero cross-patient reachability.**
