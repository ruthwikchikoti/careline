# Blind emergency batteries — the honest generalisation number

The committed eval set (`../cases/`) and the regression probes in
`tests/brain/` measure what each release was **built to pass**. They cannot
tell you how the rail does on wording nobody tuned against. A blind battery can.

## Protocol
1. A fresh evaluator agent that has **never read the rails' code** writes the
   battery (current emergencies + benign near-misses) **before** opening any repo file.
2. Only then is it scored against the active policy with
   `python -m scripts.score_blind evals/blind/battery-N.json`.
3. The result is recorded against that **one** policy version. If the next
   release is tuned on a battery's misses, that battery becomes **dev data**
   and the release after needs a fresh battery. A battery number is only quoted
   for the version it was blind to.

## Results

| Battery | Blind to | n (emergency / benign) | Recall | False escalation | Emergencies answered | Raw results |
|---|---|---|---|---|---|---|
| `battery-1.json` | `red_flags@v4` | 40 / 40 | **34/40 (85%)** | 4/40 (10%) | 0 | `battery-1.results-v4.json` |
| `battery-2.json` | `red_flags@v5+d7897fb5e5ab` | 50 / 50 | **42/50 (84%)** | 13/50 (26%) | 0 | `battery-2.results-v5.json` |

`battery-1` was used to build v5 (its 6 misses and 4 false escalations are eval
items `em-113..118`, `oos-056..059`), so its v5 score (40/40, 0/40) is **fit, not
evidence** — do not quote it. `battery-2` is the current honest number.

## What the numbers say
- Two independent blind batteries agree: the keyless deterministic rail catches
  **~84–85%** of freshly-worded emergencies. That is the ceiling of a lexical +
  structural rail without a model; the remaining misses (infant fever, head
  injury on anticoagulants, Hinglish ideation, a bee sting with throat
  symptoms, alcohol + sedatives) are why the LLM path exists and why the
  limitation is disclosed rather than tuned away.
- **No blind emergency has ever ended in ANSWER.** Every miss ended in a
  CLARIFY redirect, and every redirect carries the emergency line
  ("If this is an emergency, call 112 (India) or your local emergency number now.").
- The cost of recall is over-escalation: 26% of battery-2's benign near-misses
  (history, fiction, resolved symptoms) escalate, versus 7.9% on the committed
  eval set — the committed benign split is easier than real near-misses, and we say so.
