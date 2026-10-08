# Labelling agreement

*Generated:* 2026-10-08 17:07 UTC · `python -m scripts.human_label agree` · classes EMERGENCY / ANSWER / OTHER

## Human labellers

_No human labels yet — no human κ is claimed._

## AI second labeller (NOT human)

An AI model labelled the same blind sample without seeing the reference labels. This checks that the labels are consistent and unambiguous; it is not human agreement, and an AI from the same model family helped write many items, so the two are not independent.

| Labeller | Items labelled | Agreement with reference | Cohen's κ vs reference | Missed EMERGENCY (reference EMERGENCY, labeller not) | Labeller ANSWER where reference not |
|---|---|---|---|---|---|
| claude | 60 | 60/60 | 1.00 | 0 | 0 |

### Disagreements with the reference

| Labeller | Item | Reference | Labeller |
|---|---|---|---|

Each disagreement is reviewed: fix the reference label (state before/after in the commit) or record why the reference stands.

