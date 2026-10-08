"""Typo normalisation for the highest-risk emergency tokens (policy v4).

A caller in distress types fast: "chest pian", "chestpain", "unconcious",
"seizur". The literal and semantic rails match spelled words, so a one-letter
slip in exactly the word that matters walked past them at v3. This module
produces a *normalised variant* of the question in which near-misses of a
small, curated set of high-risk tokens are snapped to the canonical spelling.
The rails then scan BOTH the original and the normalised text — normalisation
can only add matches, never remove one.

Constraints (false positives are safe, but not free):

* **Deterministic, keyless, tiny.** Optimal-string-alignment edit distance
  (Damerau: one transposition counts as one edit) over a short canonical list.
* **Only distinctive tokens** are fuzzily matched: an input token must have
  >= 5 letters (``_snap`` skips shorter ones), and it snaps to a canonical
  token (all canonical tokens are >= 7 letters) at distance <= 1 (<= 2 when
  the canonical token has >= 10 letters). Words whose one-edit neighbours are
  everyday English were deliberately left OUT of the list — "fainted"
  (painted), "choking" (cooking), "wheezing" (sneezing).
* **Bigram repairs** for the two short high-risk compounds: the token after
  "chest" snaps to "pain", the token after "heart" snaps to "attack", and the
  run-together forms "chestpain" / "heartattack" are split.

Mirrored into ``backend/policies/red-flags.v5.yaml`` (``typo_normalisation``;
sync enforced by ``tests/llm/test_prompt_registry.py``).

Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

import re

TYPO_CANONICAL_TOKENS: tuple[str, ...] = (
    "unconscious",
    "unresponsive",
    "seizure",
    "seizures",
    "convulsion",
    "convulsions",
    "suicidal",
    "suicide",
    "overdose",
    "overdosed",
    "anaphylaxis",
    "anaphylactic",
    "breathe",
    "breathing",
    "bleeding",
    "vomiting",
)

# (head word, canonical next word): the next token is repaired at distance <= 1.
TYPO_BIGRAMS: tuple[tuple[str, str], ...] = (("chest", "pain"), ("heart", "attack"))

_WORD = re.compile(r"[A-Za-z]+")
_JOINED = re.compile(r"\b(chest|heart)(pain|attack)\b", re.IGNORECASE)


def _osa_distance(a: str, b: str, cap: int) -> int:
    """Optimal-string-alignment distance, early-exiting once it exceeds ``cap``."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev2: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if (
                prev2 is not None and i > 1 and j > 1
                and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]
            ):
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[-1]


def _snap(token: str) -> str:
    low = token.lower()
    if len(low) < 5 or low in TYPO_CANONICAL_TOKENS:
        return token
    for canon in TYPO_CANONICAL_TOKENS:
        cap = 2 if len(canon) >= 10 else 1
        if _osa_distance(low, canon, cap) <= cap:
            return canon
    return token


def normalise_typos(question: str) -> str:
    """Return ``question`` with high-risk near-miss tokens snapped to canonical.

    Returns the input unchanged when nothing needed repair, so callers can
    cheaply skip a second scan.
    """
    if not question:
        return question
    text = _JOINED.sub(lambda m: f"{m.group(1)} {m.group(2)}", question)
    words = list(_WORD.finditer(text))
    out: list[str] = []
    last = 0
    prev_low = ""
    for m in words:
        tok = m.group(0)
        fixed = tok
        for head, canon in TYPO_BIGRAMS:
            if prev_low == head and tok.lower() != canon and _osa_distance(tok.lower(), canon, 1) <= 1:
                fixed = canon
                break
        else:
            fixed = _snap(tok)
        out.append(text[last : m.start()])
        out.append(fixed)
        last = m.end()
        prev_low = tok.lower()
    out.append(text[last:])
    return "".join(out)


def text_variants(question: str) -> tuple[str, ...]:
    """The texts every rail scans: the original, plus the typo-normalised
    variant when it differs."""
    fixed = normalise_typos(question or "")
    return (question,) if fixed == question else (question, fixed)


__all__ = ["TYPO_CANONICAL_TOKENS", "TYPO_BIGRAMS", "normalise_typos", "text_variants"]
