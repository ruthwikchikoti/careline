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

Policy v6 (round-4 red team, 2026-10-08) adds two things:

* **De-obfuscation** (:func:`deobfuscate`). "c-h-e-s-t p-a-i-n", "I c a n ' t
  b r e a t h e" and "ch3st p@in" walked past every rail at v5 and were
  ANSWERED when appended to an in-scope question. Runs of >= 3 single
  letters separated by single spaces or by ``- . _ *`` are collapsed, and
  inside alphabetic words (>= 3 letters, no unmappable digit, not a
  mixed-case code like "HbA1c") the leetspeak characters ``3 @ 0 1 $`` are
  read as ``e a o i s``. Numbers, doses ("500mg"), short codes ("B12") and
  emergency numbers ("112") are untouched.
* **Fuzzy pair repair** (:data:`TYPO_FUZZY_PAIRS`): a near-miss of BOTH words
  of a high-risk pair ("hart atack", "chst pian", "passd out") is snapped
  to the canonical pair; at v5 only the second word was repaired, and only
  after an exactly-spelled head word.

Policy v7 (final red team, 2026-10-08) adds **informal-spelling repair**
(:func:`restore_informal`): "Ive been havin fits all mornin" walked past every
rail at v6 while "I've been having fits all morning" did not. Dropped-g
forms of a closed list of body / distress verbs and time words
(:data:`DROPPED_G_WORDS`: "havin", "fittin'", "seizin", "shakin", "mornin")
get their g back, and apostrophe-less contractions (:data:`APOSTROPHE_LESS`:
"Ive", "cant", "wont", "hes", "isnt") get their apostrophe back. Closed
lists only — no generic "-in" -> "-ing" rewrite ("cabin", "begin" stay).

Mirrored into ``backend/policies/red-flags.v8.yaml`` (``typo_normalisation``;
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

# v6: (head, tail) pairs where BOTH words may be misspelled. The head must be
# >= 4 letters and within distance 1 of the canonical head; the tail within
# distance 1 (exact when the canonical tail is shorter than 4 letters).
TYPO_FUZZY_PAIRS: tuple[tuple[str, str], ...] = (
    ("heart", "attack"),
    ("chest", "pain"),
    ("passed", "out"),
)

# v6 leetspeak map, applied only inside alphabetic words (see deobfuscate).
LEET_MAP: dict[str, str] = {"3": "e", "@": "a", "0": "o", "1": "i", "$": "s"}

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


def _near(token: str, canon: str) -> bool:
    if token == canon:
        return True
    if len(canon) < 4:
        return False
    return _osa_distance(token, canon, 1) <= 1


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
    prev_idx = -1  # index in ``out`` of the previous word's text
    for m in words:
        tok = m.group(0)
        low = tok.lower()
        fixed = tok
        for head, canon in TYPO_BIGRAMS:
            if prev_low == head and low != canon and _osa_distance(low, canon, 1) <= 1:
                fixed = canon
                break
        else:
            for head, tail in TYPO_FUZZY_PAIRS:
                if (
                    prev_idx >= 0
                    and len(prev_low) >= 4
                    and (prev_low != head or low != tail)
                    and _near(prev_low, head)
                    and _near(low, tail)
                ):
                    out[prev_idx] = head
                    fixed = tail
                    break
            else:
                fixed = _snap(tok)
        out.append(text[last : m.start()])
        out.append(fixed)
        prev_idx = len(out) - 1
        last = m.end()
        prev_low = low
    out.append(text[last:])
    return "".join(out)


# -- v6 de-obfuscation -------------------------------------------------------
# Runs of >= 3 single letters joined by one separator: "c-h-e-s-t",
# "c.h.e.s.t", "b r e a t h e", "I c a n ' t" (the apostrophe counts as a
# letter so contractions survive).
_SPACED_RUN = re.compile(r"(?<!\S)(?:[A-Za-z'] ){2,}[A-Za-z'](?!\S)")
_SEPARATED_RUN = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z'][-._*]){2,}[A-Za-z'](?![A-Za-z0-9])")
_LEET_TOKEN = re.compile(r"[A-Za-z0-9@$]+")
_UNMAPPABLE_DIGIT = re.compile(r"[2456789]")


def _collapse_spaced(m: re.Match[str]) -> str:
    run = m.group(0).replace(" ", "")
    # "I c a n ' t" -> "I can't": a leading capital I followed by lower case
    # is the pronoun, not the first letter of the word.
    if len(run) > 2 and run[0] == "I" and run[1].islower():
        return "I " + run[1:]
    return run


def _unleet(m: re.Match[str]) -> str:
    tok = m.group(0)
    letters = [c for c in tok if c.isalpha()]
    if (
        len(letters) < 3
        or not any(c in LEET_MAP for c in tok)
        or _UNMAPPABLE_DIGIT.search(tok)
        # Mixed-case codes (HbA1c, SpO2) are not leetspeak.
        or any(c.isupper() for c in tok[1:]) and any(c.islower() for c in tok)
    ):
        return tok
    return "".join(LEET_MAP.get(c, c) for c in tok)


def deobfuscate(question: str) -> str:
    """Collapse letter-spaced / separator-spaced runs and read leetspeak
    inside alphabetic words (policy v6). Returns the input unchanged when
    nothing applied."""
    if not question:
        return question
    text = _SEPARATED_RUN.sub(lambda m: re.sub(r"[-._*]", "", m.group(0)), question)
    spaced = _SPACED_RUN.sub(_collapse_spaced, text)
    if spaced != text:
        # Letter-spaced text marks word breaks with 2+ spaces; collapse them.
        spaced = re.sub(r" {2,}", " ", spaced)
    return _LEET_TOKEN.sub(_unleet, spaced)


# -- v7 informal-spelling repair ---------------------------------------------
#: Canonical -ing words whose dropped-g form ("havin", "fittin'") is restored.
DROPPED_G_WORDS: tuple[str, ...] = (
    "having", "fitting", "seizing", "shaking", "jerking", "twitching", "convulsing",
    "bleeding", "breathing", "choking", "gasping", "wheezing", "vomiting", "puking",
    "throwing", "fainting", "collapsing", "falling", "dying", "burning", "sweating",
    "shivering", "trembling", "swelling", "hurting", "paining", "passing", "peeing",
    "foaming", "drooling", "spinning", "losing", "turning", "going", "getting", "feeling",
    "keeping", "stopping", "starting", "morning", "evening", "nothing",
)
#: Apostrophe-less contractions -> the spelled form.
APOSTROPHE_LESS: dict[str, str] = {
    "ive": "I've", "im": "I'm", "cant": "can't", "wont": "won't", "dont": "don't",
    "doesnt": "doesn't", "didnt": "didn't", "isnt": "isn't", "wasnt": "wasn't",
    "couldnt": "couldn't", "hasnt": "hasn't", "havent": "haven't", "arent": "aren't",
    "hes": "he's", "shes": "she's", "theyre": "they're", "youre": "you're",
}

_DROPPED_G = re.compile(
    r"\b(" + "|".join(w[:-1] for w in DROPPED_G_WORDS) + r")(?:'|\u2019)?(?![\w'\u2019])",
    re.IGNORECASE,
)
_NO_APOSTROPHE = re.compile(r"\b(" + "|".join(APOSTROPHE_LESS) + r")\b", re.IGNORECASE)


def restore_dropped_g(question: str) -> str:
    """'havin' / "fittin'" -> 'having' / 'fitting' for :data:`DROPPED_G_WORDS` (v7)."""
    if not question:
        return question
    return _DROPPED_G.sub(
        lambda m: m.group(1) + ("G" if m.group(1).isupper() else "g"), question
    )


def restore_apostrophes(question: str) -> str:
    """'Ive' / 'wont' -> "I've" / "won't" for :data:`APOSTROPHE_LESS` (v7)."""
    if not question:
        return question
    return _NO_APOSTROPHE.sub(lambda m: APOSTROPHE_LESS[m.group(1).lower()], question)


def restore_informal(question: str) -> str:
    """Both v7 informal-spelling repairs; the input unchanged when nothing applied."""
    return restore_apostrophes(restore_dropped_g(question))


def text_variants(question: str) -> tuple[str, ...]:
    """The texts every rail scans: the original, plus (v6) the de-obfuscated
    variant and the typo-normalised variant of each, when they differ; v7 runs
    the informal-spelling repair inside the de-obfuscated variant.
    Variants can only add matches, never remove one."""
    question = question or ""
    out: list[str] = [question]
    clean = restore_informal(deobfuscate(question))
    for base in (question, clean):
        for variant in (base, normalise_typos(base)):
            if variant not in out:
                out.append(variant)
    return tuple(out)


__all__ = [
    "APOSTROPHE_LESS",
    "DROPPED_G_WORDS",
    "LEET_MAP",
    "TYPO_CANONICAL_TOKENS",
    "TYPO_BIGRAMS",
    "TYPO_FUZZY_PAIRS",
    "deobfuscate",
    "normalise_typos",
    "restore_apostrophes",
    "restore_dropped_g",
    "restore_informal",
    "text_variants",
]
