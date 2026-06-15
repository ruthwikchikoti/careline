# Contributing — CareLine

Working agreements for the 5-member capstone team. The fuller product/planning docs
live in [`../capstone/`](../capstone); this file is the short, in-repo version every
commit follows.

## 1. Owned paths (commit only inside yours)
| Member | Scope(s) | Owned paths (under `backend/careline/`) |
|---|---|---|
| **Ruthwik** | `graph`, `brain`, `repo` | `adapters/orchestration/`, `domain/brain/`, `domain/model/decision.py`, `domain/enums.py`; repo scaffold |
| **Srujan** | `llm` | `adapters/llm/`, `adapters/factory.py`, `domain/ports/reasoning.py`, `domain/model/proposal.py` |
| **Naga** | `data` | `adapters/mongo/`, `adapters/memory/`, `domain/model/{fact,temporal,patient,consent,consultation}.py`, `domain/ports/{memory,repositories}.py` |
| **Naresh** | `api`, `track-a` | `services/*_service.py`, `services/hard_purge.py`, `api/`, `adapters/auth/`, `config.py` |
| **Vinay** | `safety`, `eval` | `domain/gates/`, `domain/scoring/`, `domain/rails/`, `domain/thresholds.py`, `domain/model/call_session.py`, `adapters/telephony/`, `adapters/llm/tracing.py`, `services/question_service.py`, `services/audit_service.py`, `tests/brain/test_bakeoff_*` |

Stage **only** files inside your owned paths. A shared file (`CLAUDE.md`,
`api/app.py` DI wiring, `adapters/factory.py`) is edited by its designated owner
after a quick heads-up, so `git blame` stays clean.

## 2. Commit convention
```
<type>(<scope>): <imperative summary ≤ 72 chars>

<why this change; what it enables; any tradeoff>

Refs: <TASK-ID>          # e.g. Refs: RU-2
```
- `type` ∈ `feat | fix | test | docs | refactor | chore`
- `scope` = your member scope (`graph`, `brain`, `llm`, `data`, `api`, `safety`, `eval`, `repo`).
- For safety-critical work (rails / gates / brain), commit the **test first** as its
  own commit, then the implementation.
- Commit under your own git identity so each vertical's ownership is clear:
  ```bash
  git config user.name "Ruthwik"
  git config user.email "you@example.com"
  ```

## 3. Green before every commit
```bash
cd backend && python -m pytest -q
```
Never push red to the shared branch.

## 4. The one rule the whole codebase serves
> **Uncertainty always resolves toward ESCALATE. Never answer from a superseded
> fact. One patient per call — zero cross-patient reachability.**
