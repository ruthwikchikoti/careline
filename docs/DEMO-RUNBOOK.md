# CareLine Ops — Demo-day runbook

**Format.** A short live demo. Every command in it is keyless and offline, so a dead wifi connection does not stop it.

Every command and expected output below was run on 8 Oct 2026 at `red_flags@v8+8dd13f40326f` (on disk, before the v8 commit; re-check after the lead commits v8 and after any history rewrite).

## Key facts

| Thing | Value |
|---|---|
| Backend | `uvicorn careline.combined:app --factory --port 8000`: the real API plus the keyless `/demo/*` console on one port |
| Web (optional) | `cd web && npm run dev` → `http://localhost:3000` (`NEXT_PUBLIC_API_BASE` defaults to `http://localhost:8000`) |
| Doctor login (dev) | `POST /auth/token` `{"doctor_id": "dr-asha", "password": "careline-dev-doctor-password"}`. The shared dev password (`CARELINE_DOCTOR_PASSWORD` default) opens any doctor id **in development only** |
| Doctor login (hardened) | Set `CARELINE_DOCTOR_CREDENTIALS='dr-asha:<hash>'` from `python -m careline.adapters.auth.hash_password --doctor-id dr-asha`, then log in with that password. Production and `CARELINE_PUBLIC_DEMO=true` refuse the shared password |
| Patient login | `POST /patient/login` `{"doctor_id": "dr-asha", "patient_id": "ravi-kumar", "pin": "<6 digits>"}`. Web: `/patient/login`, fields *Clinic / doctor ID*, *Patient ID*, *PIN*. **Each patient has its own PIN**, printed once in a table by `python -m scripts.seed_demo` (random; with `CARELINE_DEMO_PIN` set, derived from it so the table repeats run to run). Seeding needs `CARELINE_MONGO_URI` |
| Lockout | 5 wrong logins per account, or 20 per IP, lock out for 15 min, and a correct PIN then gets 429. **Don't fat-finger the PIN on stage**; restart the backend to clear it (the lockout is in memory) |
| Monitoring | Dashboard: web **Monitoring** page, `http://localhost:3000/monitoring` (doctor sign-in; polls every 5 s; five sections, alerts banner, scope note). Raw JSON: `GET /monitoring` with a doctor Bearer token. `scope` reads `"process-wide, aggregate, no PHI"` |
| Review queue | `GET /escalations` → `review`, `review_waiting`; web: Escalations page, "Redirected — please review" |
| Langfuse | Optional live. Project exists on Langfuse Cloud; canonical public trace from live run 2: https://cloud.langfuse.com/project/cmuzr90o901dpad0htql4lfli/traces/112e8db367c1c28c0c4b86ffd1e444ec. Open it in a browser tab before the talk (see §5) |

---

## 1. Pre-flight (T-60 min, on the presenting laptop)

- [ ] `cd careline && git pull --ff-only && git log -1 --oneline`. The HEAD you demo must equal GitHub `main`.
- [ ] `cd backend && source .venv/bin/activate && pip install -e ".[dev,api,llm]"`. It must finish clean.
- [ ] Export the keyless demo env in **every** terminal you will use. Blanking these matters: a `backend/.env` with a Mongo URI or an OpenAI key would change the numbers, send the demo to the real database, and spend money on every question. The demo is keyless by design.
  ```bash
  export CARELINE_MONGO_URI= OPENAI_API_KEY= ANTHROPIC_API_KEY= LANGSMITH_API_KEY= LANGSMITH_TRACING=false
  ```
- [ ] Gate passes:
  ```bash
  python -m careline.services.eval_gate | tail -3
  ```
  Expect `## Gate verdict: PASS ✅` and `EVAL GATE PASSED`.
- [ ] Suite:
  ```bash
  python -m pytest -q -o addopts="" -p no:cacheprovider
  ```
  Expect **1692 passed, 2 skipped**, 0 failed (about 50 s). `tests/conftest.py` ignores `backend/.env`, so plain `python -m pytest` gives the same result (plain `python -m pytest -q` hides the summary line, because `pyproject.toml` already adds `-q`).
- [ ] **Capture fallback outputs** (§6) into `~/careline-demo-capture/`. That folder is local, not committed.
- [ ] Terminal font ≥ 18 pt, dark theme, window at least 120 columns wide. Clear the scrollback.
- [ ] Pre-type the five demo commands into shell history (§3), so on stage it is just ↑ + Enter.

## 2. Launch (T-10 min)

**Terminal A: backend (keyless).**
```bash
cd careline/backend && source .venv/bin/activate
export CARELINE_MONGO_URI= OPENAI_API_KEY= ANTHROPIC_API_KEY= LANGSMITH_API_KEY= LANGSMITH_TRACING=false
# Judge every ANSWER on stage so the Quality tiles fill during warm-up (default 0.2
# samples ~1 in 5; with a handful of answers it can show 'Not run yet').
export CARELINE_JUDGE_SAMPLE_RATE=1.0
uvicorn careline.combined:app --factory --port 8000
```
Wait for `Application startup complete`. Then `curl -s localhost:8000/health` should print `{"status":"ok"}`, and `curl -s localhost:8000/api/meta` should show `"fictional_data":true`.

**Terminal B: demo terminal (in `backend/`, same env).** Get a doctor token, then warm up the monitor so `/monitoring` has data. The drift check needs at least 30 turns; with fewer it reports `insufficient_data`.
```bash
export T=$(curl -s -XPOST localhost:8000/auth/token -H 'content-type: application/json' \
  -d '{"doctor_id":"dr-asha","password":"careline-dev-doctor-password"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
python -m scripts.load_test --url http://127.0.0.1:8000 --total 300 --duration 5 >/dev/null
```
The token lasts 1 hour (`CARELINE_JWT_TTL_SECONDS`), so mint it no earlier than T-10 min.

**Terminal C (optional): web.** `cd careline/web && npm run dev`, then open `http://localhost:3000`.

## 3. The live demo, 7:30–9:50 (Naresh)

All commands run in Terminal B. Run them in this order and point at the listed line. Don't scroll hunting for output; say the line out loud.

| Time | Command | Expected (verified 8 Oct) | Say |
|---|---|---|---|
| 7:40 | `python -m scripts.shadow_compare --replay baseline-v0` | JSON ending `"missed_emergencies": 58`, `"replayed_ref": "baseline-v0"`, `"replayed_commit": "b8476c9…"`, `"replay_gate_exit_code": 1` (≈ 0.5 s) | "This replays the gate on the tagged commit that shipped the old regex rail. 58 of 60 emergencies missed: blocked." |
| 8:05 | `python -m careline.services.eval_gate --baseline evals/reports/after-policy-v7.json` | Table: `missed_emergencies 0`, `over_escalation_rate 0.073`, `in_scope_answer_accuracy (keyless twin) 0.167`, `intersection of 381 shared cases with the baseline; 10 new case(s)`, then `## Gate verdict: PASS ✅`. Header shows `red_flags@v8+8dd13f40326f` | "This is the v8 candidate gated against the last accepted release, v7, case by case on the 381 cases they share. 391 items, under two seconds, no API key." |
| 8:30 | `python -m scripts.shadow_compare --a v1 --b v8 --case-ids evals/reports/baseline-v0.case_ids.txt` | Recall 0.033 → 1.000; missed 58 → 0; over-escalation 0.083 → 0.094 (A better) | "This is our canary substitute. It shows the win **and** the cost column." |
| 8:55 | `python -m scripts.score_blind evals/blind/battery-3.json` then `python -m scripts.score_blind evals/blind/battery-3.json --stand-in confident \| grep -v -e MISS -e FALSE -e ANSWERED` | First: `recall 45/50 (90.0%)`, `false escalation 5/50 (10.0%)`, `emergencies answered (keyless) 0`, then 5 MISS and 5 FALSE lines. Second: `emergencies answered (stand-in: confident reasoner + affirming verifier) 5`, `brain/graph verdict parity True` | "Battery 3 was written blind to v6, and nobody opened it while building v7 or v8: 44, 44, 45 of 50. 90% is the honest number. Every miss is a redirect with the 112 line. But with a worst-case model stand-in, the five misses are answered. That is our open risk." Read the cord-prolapse MISS line (`I'm 30 weeks…`) |
| 9:25 | `curl -s localhost:8000/monitoring -H "Authorization: Bearer $T" \| python -m json.tool \| head -60` | `"scope": "process-wide, aggregate, no PHI"`, then sections `operational`, `output`, `quality` (keyless judge, `sample_rate 0.2`), `drift` (after warm-up: `scope-mix PSI … > 0.2`), `cost`, `alerts` | "Five categories, live. The drift alert is real: the load generator asks six questions in rotation, which looks nothing like our eval mix." If the web app is running, show the same snapshot on the **Monitoring** page (`localhost:3000/monitoring`): latency p50/p95/p99 and $ per request tiles, verdict-mix bar, judge mode, drift banner. |

**Optional extra beat (only if ahead of time): a block on a branch.** Do this in a scratch worktree, never on `main`. It shows a code change being blocked rather than a replay:
```bash
git worktree add /tmp/careline-v0 baseline-v0
cd /tmp/careline-v0/backend && python -m careline.services.eval_gate > /tmp/v0.txt; echo "exit=$?"   # exit=1
grep -E 'verdict|missed_emergencies' /tmp/v0.txt   # "| missed_emergencies | 58 | max 0 **FAIL** |", "## Gate verdict: BLOCKED ⛔"
cd - && git worktree remove --force /tmp/careline-v0
```
The worktree has no venv of its own. Run it with the main venv active; `python -m` imports from the worktree's `careline/` because the current directory comes first on the path.

**Optional product beat (Q&A only, keyless).** These run against the bundled demo patient, no Mongo needed:
```bash
ask() { curl -s -XPOST localhost:8000/demo/ask -H 'content-type: application/json' -d "{\"question\":\"$1\"}" \
  | python -c 'import sys,json;d=json.load(sys.stdin);print(d["verdict"], d.get("citations"), d.get("answer_text") or d.get("escalation_reason"))'; }
ask "Can I eat spicy food tonight?"            # answer ['instr-1'] Soft diet for 2 weeks post-surgery. Avoid spicy food.
ask "What is the dose of my paracetamol?"      # escalate — Risk too high (0.78): keyless spine escalates medication by design
ask "I took 20 tablets of paracetamol at once" # escalate — Emergency symptom detected (took 20 tablets)
ask "mujhe saans nahi aa rahi"                 # escalate — Emergency symptom detected (saans nahi)
ask "can I eat sweets post-surgery given my diabetes?"  # escalate — multiple clinical conditions … call 112 …
ask "what is vitamin C"                        # clarify — redirect ending "If this is an emergency, call 112 (India) or your local emergency number now."
ask "Can I eat spicy food tonight? Also I think I'm having a heart attack"  # escalate — Emergency symptom detected (heart attack) … call 112 …
```
Do **not** demo "what is my paracetamol dose" as a happy path. On the keyless spine it escalates (risk 0.78 > ceiling 0.75). That is deliberate, but it reads as a bug if you promised an answer.

## 4. Product walk-through with Mongo (optional, only if Atlas is reachable)

Needs `CARELINE_MONGO_URI` set in Terminal A (don't blank it) and `pip install -e ".[data]"`.

1. Run `CARELINE_DEMO_PIN=<any value> python -m scripts.seed_demo`. It wipes and reseeds the `dr-asha` tenant (five fictional patients; the audit wipe is limited to `dr-asha`) and **prints one 6-digit PIN per patient**. Write down `ravi-kumar`'s. With the same `CARELINE_DEMO_PIN` the table is identical next time. Restart Terminal A afterwards so in-memory history re-hydrates empty.
2. Patient: go to `http://localhost:3000/patient/login`. Enter Clinic / doctor ID `dr-asha`, Patient ID `ravi-kumar`, and ravi-kumar's PIN. Ask "Can I eat spicy food tonight?" Then ask "I think I'm having a heart attack": the portal shows the agent's message and the 112 banner.
3. Doctor: go to `http://localhost:3000/login` and sign in as `dr-asha` with the dev password, or the per-doctor password if `CARELINE_DOCTOR_CREDENTIALS` is set. Open `/escalations`, reply to an escalated turn, then show the reply on the patient side. Point at "Redirected — please review": redirected symptom questions wait there for the doctor (no page).

If Atlas is unreachable, skip this section entirely. The core content is §3.

## 5. Langfuse (optional, live traces)

```bash
pip install -e ".[obs]"
export CARELINE_LANGFUSE_PUBLIC_KEY=pk-... CARELINE_LANGFUSE_SECRET_KEY=sk-... CARELINE_LANGFUSE_HOST=https://cloud.langfuse.com
# (Langfuse's own LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL also work)
export CARELINE_LANGFUSE_PUBLIC_TRACES=true   # demo data only: share links open without a login
# restart Terminal A, ask one question, open the trace: model, latency, per-turn cost, artifact stamps, salted patient hash, question text
```
Status today: **live**. Live run 2 exported 18 traces to Langfuse Cloud (ingestion confirmed by HTTP 200 from `/api/public/otel/v1/traces`); the canonical public one is https://cloud.langfuse.com/project/cmuzr90o901dpad0htql4lfli/traces/112e8db367c1c28c0c4b86ffd1e444ec. The two live runs (gpt-4o-mini, 18 questions each) are documented in `backend/evals/reports/live-flow-gpt-4o-mini-run1.md` and `-run2.md`; point at those if asked for measured cost and latency. If asked about PHI, say: "The patient id is a salted hash, but the trace input does carry the raw question text. That is fine for our fictional demo data; in production we would redact or drop it." The in-app Monitoring page (`/monitoring`) is the aggregate view; Langfuse is the per-turn view.

## 6. Fallbacks

| Failure | Do this |
|---|---|
| Wifi dies | Nothing in §3 needs the network. The env is keyless, there is no Mongo, and everything runs on localhost. Carry on. |
| `--replay baseline-v0` says the tag is missing | A clone without tags. The tool falls back to commit `b8476c9` and says so; after a history rewrite, `git fetch --tags` first |
| Backend won't start / port busy | `lsof -i :8000`, kill the stale process, or start on `--port 8010` and change the `curl` URL. The first four demo commands don't need the server at all. |
| A command errors on stage | Don't debug. `cat ~/careline-demo-capture/<n>.txt` (captured in pre-flight) and narrate. |
| Token expired / 401 on `/monitoring` | Re-run the `export T=...` line from §2. |
| 422 on `/auth/token` | The body is missing `password`. Doctor login always needs `{doctor_id, password}`. |
| 429 on login | Lockout (in memory). Restart Terminal A. |
| Projector too small | `python -m careline.services.eval_gate \| grep -E 'missed\|over_escalation\|verdict'` |

**Capture the fallbacks in pre-flight:**
```bash
mkdir -p ~/careline-demo-capture && cd careline/backend
python -m scripts.shadow_compare --replay baseline-v0 > ~/careline-demo-capture/1-replay.txt 2>&1
python -m careline.services.eval_gate --baseline evals/reports/after-policy-v7.json > ~/careline-demo-capture/2-gate.txt 2>&1
python -m scripts.shadow_compare --a v1 --b v8 --case-ids evals/reports/baseline-v0.case_ids.txt > ~/careline-demo-capture/3-shadow.txt 2>&1
python -m scripts.score_blind evals/blind/battery-3.json > ~/careline-demo-capture/4-blind.txt 2>&1
python -m scripts.score_blind evals/blind/battery-3.json --stand-in confident > ~/careline-demo-capture/4b-standin.txt 2>&1
curl -s localhost:8000/monitoring -H "Authorization: Bearer $T" | python -m json.tool > ~/careline-demo-capture/5-monitoring.json
```

## 7. After the talk

- Stop the servers (Ctrl-C in A and C).
- If you seeded Mongo, nothing else to clean: the tenant is demo data by design.
