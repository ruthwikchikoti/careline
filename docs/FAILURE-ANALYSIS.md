# CareLine — Failure Analysis & Improvements

Three real bugs we found while running CareLine end-to-end, the trace evidence that
exposed each one, the exact fix, and the test that now locks it in. Every commit, file,
and line reference below is real and present in this repo's history.

## How we debug

CareLine is built so that a failure is *legible*, not a black box. Two layers of
evidence drive every debugging session:

- **The always-on `ReasoningTrace`.** Every rail and gate appends a structured step to a
  per-turn trace — a step name, a `TraceStatus` (`PASS` for a step that let the turn
  proceed, `TERMINAL` for the step that decided the verdict, `SKIPPED` for a step
  short-circuited upstream; see `backend/careline/domain/enums.py:58`), a spec-section
  reference (e.g. `§5.1`), and a human-readable detail. This trace renders in the **Live
  Console** and the **Audit UI**, so when a turn does the wrong thing we can read exactly
  which gate fired and why, without a debugger.
- **LangSmith spans on the live path.** The application service wraps the live LLM flow
  in nested spans — `question_service.run_question` → `reasoner.propose` →
  `verifier.verify` (`backend/careline/services/question_service.py:86,179,198`). When the
  ReasoningTrace says "low confidence → escalate", the spans tell us the actual reasoner
  and verifier confidences and citations that produced that number.

The three bugs below were each first *seen* in one of these surfaces (a flooded
escalation queue in the Audit UI; a confidence number in the spans below the floor) and
then traced to a specific line of domain logic.

---

## Bug 1 — Rich-record patients were escalated on every question

**Commit:** `fb85a86` *fix(scoring): grounding measures citation validity, not coverage*

**Symptom.** Patients with a fuller approved record got *worse* answers, not better:
every question — even a clearly answerable one — fell below the 0.7 confidence floor and
was escalated to the doctor. A patient with two valid facts who asked about one of them
was punished for the fact they *didn't* ask about.

**Root cause.** The grounding component of the confidence score measured *coverage* of
the slice, not whether the citations were real. The original formula in
`backend/careline/domain/scoring/confidence.py` was:

```python
grounding = (
    min(1.0, len(proposal.citations) / valid_slice.count)
    if valid_slice.count > 0
    else 0.0
)
```

A legitimate one-fact answer against a four-fact record scored `1/4 = 0.25` grounding.
Fed into the weighted geometric mean (grounding weight `0.3`), that dragged the whole
score under the floor — so the more context a doctor approved, the more certain the
escalation.

**Fix.** Grounding now measures citation *validity*: of the facts the reasoner cited, how
many actually exist in the currently-valid slice (`confidence.py:111-117`):

```python
valid_ids = {fact.id for fact in valid_slice.facts}
citations = proposal.citations
grounding = (
    sum(1 for cid in citations if cid in valid_ids) / len(citations)
    if citations
    else 0.0
)
```

A correct one-fact answer now scores `1.0` grounding regardless of record size, while a
**fabricated or superseded** citation — the real hazard — still drags the score down, and
the verifier hard-zeros it independently. The safety property is preserved; only the
spurious penalty is gone.

**Test that locks it.** `tests/brain/test_bakeoff_safety.py::TestT5HappyPath`. Its
fixture `_seed_patient()` is a deliberately *rich* record — a valid medication (`med-1`),
a valid instruction (`instr-1`), plus two superseded facts. The test cites only `med-1`
and asserts `Verdict.ANSWER` (`test_bakeoff_safety.py:261-286`). Under the old formula
that single citation against a multi-fact slice would have scored `≈0.5` grounding and
escalated; the test now guarantees a rich record still answers.

---

## Bug 2 — Greetings and general questions flooded the doctor's queue

**Commit:** `3194c05` *fix(safety): stop escalating non-clinical input — greet small
talk, redirect out-of-scope*

**Symptom.** The escalation queue in the Audit UI filled with noise: a patient typing
"hey" or "good morning", or asking a general-knowledge question like "what is vitamin
C", produced a **doctor escalation**. Doctors were being paged for pleasantries, which
trains them to ignore the queue — itself a safety hazard.

**Root cause.** Two gaps. (1) There was no pre-LLM rail for small talk, so a greeting
reached the reasoner, was classified `out_of_scope` (no fact establishes "hey"), and hit
the scope gate. (2) The scope gate escalated *everything* out-of-scope:

```python
return Decision.escalate(
    "Question is outside the doctor's established scope for this patient.",
    scope=ScopeCategory.OUT_OF_SCOPE,
    risk=0.8,
    trace=ctx.trace,
)
```

**Fix.** A new narrow conversational rail catches *pure* small talk before the LLM and
returns a friendly CLARIFY instead of escalating (`backend/careline/domain/rails/
conversational.py`, wired into the Brain at `brain.py` right after the red-flag rail, so
"hey, I have chest pain" still escalates). `is_small_talk` only matches a message whose
every token is a greeting/filler word, so it can never swallow a real clinical question.
The scope gate's out-of-scope branch was changed from escalate to a CLARIFY redirect
(`backend/careline/domain/gates/chain.py:90`):

```python
return Decision.clarify(
    "I can only help with the care your doctor approved for you — your "
    "medicines, diet, and post-visit instructions. For anything else, please "
    "contact the clinic directly.",
    scope=ScopeCategory.OUT_OF_SCOPE,
    trace=ctx.trace,
)
```

Emergencies (red-flag) and cross-condition questions still escalate via their own rails
upstream — only genuinely non-clinical noise is redirected.

**Test that locks it.** `tests/brain/test_bakeoff_question_service.py::TestOutOfScope::
test_out_of_scope_redirects_without_escalating` — it runs an out-of-scope question with
the clarify budget already exhausted and asserts `Verdict.CLARIFY` with
`telephony.escalations == []` (previously this test, `TestClarifyBudget`, asserted
`ESCALATE`). The behavioural contract was inverted on purpose and is now pinned.

---

## Bug 3 — Over-correction: real clinical questions got cold-redirected

**Commit:** `7a1914c` *fix(reason): route clinical-but-unanswerable to the doctor, not a
redirect*

**Symptom.** The Bug 2 fix over-shot. A genuine clinical question about *this patient's*
care that the approved facts couldn't fully answer (e.g. "is this new rash from my
medication?") was now classified `out_of_scope` and met with the polite "contact the
clinic" redirect — instead of reaching the doctor who should actually handle it.

**Root cause.** The reasoner's scope definitions conflated "we can't answer this" with
"this isn't about the patient's care." The prompt said simply:

```
- in_scope: fully answerable from the supplied facts.
- out_of_scope: the facts do not establish this.
```

So any unanswerable-but-clinical question fell into `out_of_scope` and got redirected.

**Fix.** Sharpened the scope split in `backend/careline/adapters/llm/prompts.py` so
*answerability* and *scope* are independent — in-scope means "about THIS patient's care,
even if unanswerable → a human doctor receives it"; out-of-scope means strictly
non-clinical general knowledge → redirect:

```
- in_scope: a clinical question about THIS patient's own medicines, diet,
  symptoms, or care instructions — EVEN IF the supplied facts don't fully answer it.
  ... still mark it in_scope and set candidate_answer to null, so a human doctor
  receives it.
- out_of_scope: NOT about this patient's clinical care — general medical/biology
  knowledge ("what is vitamin C"), other people, or unrelated/off-topic questions.
  These are redirected to the clinic, NOT sent to the doctor ...
```

And web turns now use a **zero clarify budget** so a clinical-but-unanswerable question
escalates straight to the doctor (who replies through the resolution loop) instead of
looping on "could you rephrase?" — `max_clarify_turns=0` in both
`backend/careline/api/routers/patient_portal.py:157` and
`backend/careline/combined.py` (`demo_ask`).

**Test that locks it.** The same `TestOutOfScope` guard from Bug 2 keeps *non-clinical*
input redirecting, while the clinical-unanswerable path is covered by the not-answerable
escalation tests — `tests/brain/test_brain.py::test_not_answerable_clarifies_then_
escalates_on_budget` and `test_empty_valid_slice_escalates` — which assert that an
in-scope question with no grounding reaches `ESCALATE` once the (now zero on web)
budget is spent.

---

## Meaningful improvements

Together these three fixes resolved a tension that made the early system unusable:

- **Rich-record patients now get answered**, not auto-escalated. Fixing the grounding
  metric (Bug 1) means the confidence floor reflects citation *validity*, so the more
  context a doctor approves, the better the agent answers — the intended behaviour.
- **The doctor's queue carries signal, not noise.** Greetings are greeted and
  general-knowledge questions are redirected (Bug 2), so escalations are now genuine
  clinical hand-offs a doctor will trust and act on.
- **Yet nothing clinical is dropped.** Bug 3 ensures that a real question about a
  patient's care that the facts can't answer still lands on the doctor, not a dead-end
  redirect.

Crucially, every fix kept the overriding rule intact: **uncertainty still resolves toward
ESCALATE.** Red-flag and cross-condition rails are untouched; the verifier veto and the
empty-slice / superseded-fact hard-zeros still force confidence to exactly `0.0`. We made
the system *usable* without trading away its fail-closed safety — and each change is
pinned by a test so the behaviour can't silently regress.
