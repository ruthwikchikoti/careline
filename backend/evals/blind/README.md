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

Wilson 95% intervals: 34/40 → 71–93%, 42/50 → 72–92%, 44/50 → 76–94%.
Battery 3 is the current honest number, and it is blind to **both** v6 and v7:
v7 was built from the final red team's findings, and nobody opened
`battery-3.json` or its results while building it (the v7 scores — keyless and
the LLM-path stand-in — were produced only after the v7 rails were frozen, and
only the aggregate counts were looked at).

## Fit, not evidence (do not quote as generalisation)

| Battery | Scored at | Recall | False escalation | Why it is a fit |
|---|---|---|---|---|
| battery-1 | v5, v6 | 40/40 | 0/40 | Its 6 misses and 4 false escalations became eval items `em-113..118`, `oos-056..059`, and v5 was built to pass them |
| battery-2 | v6, v7 | 50/50 | 13/50 (26%) | Its 8 v5 misses drove v6's new families. **v6 and v7 are not blind to battery 2.** The 13 false escalations did not move |
| battery-1 | v7 | 40/40 | 0/40 | as above |

## Recall across versions, on wording those versions never saw

Battery 3 was written after v6 and was not read while building v7, so v4, v5,
v6 and v7 are all blind to it. Battery 2 was written after v5, so v4 and v5 are
blind to it. Scored keylessly with today's `score_blind.py` on the tagged trees
(`release/red-flags-v4`, `release/red-flags-v5`) and on the working tree for v6
and v7:

| Battery | v4 | v5 | v6 | v7 |
|---|---|---|---|---|
| battery-2 | 39/50 | 42/50 | (fit) | (fit) |
| battery-3 | 39/50 | 41/50 | 44/50 | 44/50 |

## What the numbers say
- **v5 did not materially move fresh-wording recall over v4** (+3 and +2 of 50,
  inside the interval). The similar blind numbers for batteries 1 and 2 (85%,
  84%) are that plateau, not robustness. **v6 is measured by battery 3**: 88%,
  three more of 50 than v5 on the same battery, and false escalation of 10% on
  battery 3's benign near-misses. **v7 is measured by battery 3 too: 44/50 and
  5/50, unchanged.** v7's families fixed the five red-team phrasings they were
  written from and moved nothing in battery 3 — evidence that each release's
  lexical additions mostly fit the wording that drove them.
- All three batteries were written by LLM evaluator agents, not clinicians or
  patients. Their wording may be correlated with each other and easier than
  real patient phrasing.
- Battery 3's six misses at v6 (the count is the same at v7; the v7 misses were
  not inspected, to keep the battery blind): indirect self-harm ("given away my things… I won't be
  around by the weekend"), Hinglish child poisoning (phenyl), Hinglish GI bleed
  (black stool, dizziness), Hinglish pre-eclampsia, cord prolapse at 30 weeks,
  and fever on chemotherapy. Its five false escalations are all fiction, news,
  documentary or past-event framing caught by the red-flag rail.
- All ten battery-3 messages that mix an ordinary question with an emergency
  escalated.

## What happens to a miss
- **On the keyless path no blind emergency has ended in ANSWER** (0 of 140).
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
- **The LLM path is unmeasured, and the worst case is bad.** With a stand-in
  reasoner that always proposes a confident, validly cited answer and a
  verifier that always agrees, battery 3's six misses all end in **ANSWER**
  (Brain and graph agree; batteries 1 and 2 give 0, but they are dev data for
  v6). Re-run at v7: still 6 of 50 battery-3 emergencies ANSWERED (44 escalate).
  The final red team found five more such phrasings at v6; v7 fixed those (dev
  data). A real model may classify them as `red_flag` itself; no live run has
  measured that — LLM-path protection is measured only with stand-ins.
- **A paraphrased superseded dose** (v7). Separately from the rails, v7's gate
  chain refuses any ANSWER whose dose / number / drug-name tokens are not in a
  cited current fact, so even a confident, affirmed reasoner cannot repeat a
  superseded dose behind a current fact's id.

## Reproduce

```bash
cd backend
python -m scripts.score_blind evals/blind/battery-3.json     # v7 (and v6), the honest number

# a battery against an older release (v4's tree predates the scorer, so copy it in)
B=$PWD/evals/blind/battery-2.json
git worktree add /tmp/cl-v5 release/red-flags-v5
cp scripts/score_blind.py /tmp/cl-v5/backend/scripts/
(cd /tmp/cl-v5/backend && python -m scripts.score_blind "$B")   # 42/50 at v5
git worktree remove --force /tmp/cl-v5
```
