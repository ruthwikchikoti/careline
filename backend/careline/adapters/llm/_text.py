"""Tiny deterministic text utilities for the offline heuristic twins (SR-2).

Pure, dependency-free token helpers shared by the heuristic Reasoner and Verifier.
Kept private to ``adapters/llm`` (leading underscore) because this is offline
matching glue, not a domain concept — the live LLM adapters never use it.

Determinism matters: the whole keyless suite must give the same verdict on every
run, so there is no randomness, no stemming heuristics that drift, just a stable
content-word split and set overlap.
"""

from __future__ import annotations

import re

# Words that carry no grounding signal — dropped before matching so that
# "what is my paracetamol dose" reduces to {paracetamol, dose}.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "is", "are", "am", "was", "were", "be", "been", "being",
        "do", "does", "did", "doing", "to", "of", "in", "on", "at", "for", "from",
        "by", "with", "about", "as", "into", "and", "or", "but", "if", "then",
        "my", "me", "i", "you", "your", "we", "our", "it", "its", "this", "that",
        "these", "those", "what", "which", "who", "whom", "when", "where", "why",
        "how", "can", "could", "should", "would", "will", "shall", "may", "might",
        "must", "have", "has", "had", "get", "got", "im", "ive",
    }
)

# Common connective verbs/words that legitimately appear in a natural-language
# answer even after stopword removal. The Verifier tolerates these as "not a
# claim" so it vetoes on substantive ungrounded terms, not on phrasing glue.
CONNECTIVES: frozenset[str] = frozenset(
    {
        "take", "taking", "taken", "use", "using", "apply", "keep", "continue",
        "avoid", "stop", "note", "please", "every", "per", "day", "daily", "hours",
        "hour", "times", "time", "yes", "no", "also",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def content_words(text: str | None) -> frozenset[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and 1-char tokens.

    Numbers and alphanumeric tokens (``500mg``) are preserved — dose and frequency
    are exactly the grounding signal we must not throw away.
    """
    if not text:
        return frozenset()
    tokens = _TOKEN_RE.findall(text.lower())
    return frozenset(t for t in tokens if len(t) > 1 and t not in _STOPWORDS)


def overlap(query: frozenset[str], target: frozenset[str]) -> float:
    """Fraction of query content-words that appear in ``target`` — in ``[0, 1]``.

    Asymmetric on purpose: it asks "how much of the *question* is covered by this
    fact", which is the right signal for "does this fact answer the question".
    """
    if not query:
        return 0.0
    return len(query & target) / len(query)


__all__ = ["content_words", "overlap", "CONNECTIVES"]
