# Blind emergency batteries — the honest generalisation number

The committed eval set (`../cases/`) and the regression probes in
`tests/brain/` measure what each release was **built to pass**. They cannot
tell you how the rail does on wording nobody tuned against. A blind battery can.

## Protocol
1. A fresh evaluator agent that has **never read the rails' code** writes the
   battery (current emergencies + benign near-misses) **before** opening any repo file.
2. Only then is it scored against the active policy with
   `python -m scripts.score_blind evals/blind/battery-N.json` (keyless Brain,
   demo patient, clarify budget 2).
3. The result is recorded against that **one** policy version. If the next
   release is tuned on a battery's misses, that battery becomes **dev data**
   and the release after needs a fresh battery. A battery number is only quoted
   for the version it was blind to.

## Results (honest: each battery at the version it was blind to)

| Battery | Blind to | n (emergency / benign) | Recall | False escalation | Emergencies answered (keyless) | Raw results |
|---|---|---|---|---|---|---|
| `battery-1.json` | `red_flags@v4+cb141d91b062` | 40 / 40 | **34/40 (85%)** | 4/40 (10%) | 0 | `battery-1.results-v4.json` |
| `battery-2.json` | `red_flags@v5+d7897fb5e5ab` | 50 / 50 | **42/50 (84%)** | 13/50 (26%) | 0 | `battery-2.results-v5.json` |
| `battery-3.json` | `red_flags@v6+2df6ecd24fda` | 50 / 50 | **44/50 (88%)** | 5/50 (10%) | 0 | `battery-3.results-v6.json` |
| `battery-3.json` | `red_flags@v7+93b8295ea3c0` | 50 / 50 | **44/50 (88%)** | 5/50 (10%) | 0 | `battery-3.results-v7.json` |
| `battery-3.json` | `red_flags@v8+8dd13f40326f` | 50 / 50 | **45/50 (90%)** | 5/50 (10%) | 0 | `battery-3.results-v8.json` |

Wilson 95% intervals: 34/40 → 71–93%, 42/50 → 72–92%, 44/50 → 76–94%,
45/50 → 79–96%. Battery 3 is the current honest number, and it is blind to v6,
v7 and v8: v7 and v8 were built from red-team findings, and nobody opened
`battery-3.json` while building either (their scores — keyless and the LLM-path
stand-in — were produced only after the rails were frozen, and the scorer's
MISS / FALSE lines were filtered out). One caveat for v8: this README already
described battery 3's six v6 misses in words, and the one item v8 gained (the
indirect self-harm message) overlaps the red team's "goodbye letters" family.
Read the +1 cautiously. The next policy release needs a fresh battery 4.

## Fit, not evidence (do not quote as generalisation)

| Battery | Scored at | Recall | False escalation | Why it is a fit |
|---|---|---|---|---|
| battery-1 | v5, v6 | 40/40 | 0/40 | Its 6 misses and 4 false escalations became eval items `em-113..118`, `oos-056..059`, and v5 was built to pass them |
| battery-2 | v6, v7 | 50/50 | 13/50 (26%) | Its 8 v5 misses drove v6's new families. **v6, v7 and v8 are not blind to battery 2.** The 13 false escalations did not move |
| battery-1 | v7 | 40/40 | 0/40 | as above |

## Recall across versions, on wording those versions never saw

Battery 3 was written after v6 and was not read while building v7 or v8, so v4
to v8 are all blind to it. Battery 2 was written after v5, so v4 and v5 are
blind to it. Scored keylessly with today's `score_blind.py` on the tagged trees
(`release/red-flags-v4`, `release/red-flags-v5`) and on the working tree for v6,
v7 and v8. v6 and v7 landed in one commit (`1aed531`, both tags), so the v6
column cannot be regenerated from a tag; it comes from `battery-3.results-v6.json`,
scored before v7 was layered on.

| Battery | v4 | v5 | v6 | v7 | v8 |
|---|---|---|---|---|---|
| battery-2 | 39/50 | 42/50 | (fit) | (fit) | (fit) |
| battery-3 | 39/50 | 41/50 | 44/50 | 44/50 | 45/50 |

## What the numbers say
- **v5 did not materially move fresh-wording recall over v4** (+3 and +2 of 50,
  inside the interval). The similar blind numbers for batteries 1 and 2 (85%,
  84%) are that plateau, not robustness. **v6 is measured by battery 3**: 88%,
  three more of 50 than v5 on the same battery, and false escalation of 10% on
  battery 3's benign near-misses. **v7 is measured by battery 3 too: 44/50 and
  5/50, unchanged.** v7's families fixed the five red-team phrasings they were
  written from and moved nothing in battery 3 — evidence that each release's
  lexical additions mostly fit the wording that drove them. **v8: 45/50 and
  5/50** — one more, with the caveat above.
- All three batteries were written by LLM evaluator agents, not clinicians or
  patients. Their wording may be correlated with each other and easier than
  real patient phrasing.
- Battery 3's six misses at v6 (the count is the same at v7; at v8 the
  indirect self-harm item escalates and five remain; per-item results are in
  the results files, not repeated here): indirect self-harm ("given away my things… I won't be
  around by the weekend"), Hinglish child poisoning (phenyl), Hinglish GI bleed
  (black stool, dizziness), Hinglish pre-eclampsia, cord prolapse at 30 weeks,
  and fever on chemotherapy. Its five false escalations are all fiction, news,
  documentary or past-event framing caught by the red-flag rail.
- All ten battery-3 messages that mix an ordinary question with an emergency
  escalated.

## What happens to a miss
- **On the keyless path no blind emergency has ended in ANSWER** (0 of 140,
  each battery at the version it was blind to; battery 3 is also 0 at v8).
  Every miss ended in a CLARIFY redirect, and every redirect ends with "If this
  is an emergency, call 112 (India) or your local emergency number now."
- **Doctor review queue (new).** A redirected turn that names a danger concept
  or reports a present symptom is logged `needs_review=True` and appears under
  "Redirected — please review" on the doctor's Escalations page. The doctor is
  not paged; it waits in the queue. Coverage of misses:
  - battery 2 at v5: all 8 misses flagged (product-fix report);
  - **battery 3 at v6: 1 of 6 flagged** (the phenyl ingestion). The other five
    end as a redirect with the 112 line and no doctor visibility. Not
    re-measured at v7.
- **On the LLM path the worst case is bad, and the real model is unmeasured.**
  With a stand-in reasoner that always proposes a confident, validly cited
  answer and a verifier that always agrees
  (`python -m scripts.score_blind evals/blind/battery-3.json --stand-in confident`),
  battery 3's rail misses all end in **ANSWER**, Brain and graph agreeing: 6/50
  at v6 and v7, 5/50 at v8 (`battery-3.results-v8-standin.json`; batteries 1
  and 2 give 0, but they are dev data). The red teams found five more such
  phrasings at v6 (fixed by v7) and six more at v7 (fixed by v8), all dev data
  now. A real model may classify them as `red_flag` itself. The two live flow
  checks on gpt-4o-mini each sent four emergencies, all caught by the rails
  before the model; none of these misses has been sent to the real model.
- **A paraphrased superseded dose** (v7, hardened in v8). Separately from the
  rails, the gate chain refuses an ANSWER whose dose / number / drug-name
  tokens are not in a cited current fact. The final red team bypassed the v7
  version 13 ways (number words, unit conversion, "double your dose", a brand
  outside the lexicon, a reversed take/stop instruction); v8 closes all 13. It
  is still lexical, so a wrong claim with none of those tokens is left to the
  verifier.

## Reproduce

```bash
cd backend
python -m scripts.score_blind evals/blind/battery-3.json                         # v8, the honest number
python -m scripts.score_blind evals/blind/battery-3.json --stand-in confident    # LLM-path worst case

# a battery against an older release (v4's tree predates the scorer, so copy it in)
B=$PWD/evals/blind/battery-2.json
git worktree add /tmp/cl-v5 release/red-flags-v5
cp scripts/score_blind.py /tmp/cl-v5/backend/scripts/
(cd /tmp/cl-v5/backend && python -m scripts.score_blind "$B")   # 42/50 at v5
git worktree remove --force /tmp/cl-v5
```
