"""Pre-LLM conversational rail — greet small talk, don't escalate it (RU).

A greeting ("hey", "thanks", "good morning") is **not** a clinical question, so it
must never reach a doctor's escalation queue. Without this rail it would: the
Reasoner classifies it ``out_of_scope`` (no fact establishes it) and the scope gate
escalates everything out-of-scope. That is correct for a real out-of-scope clinical
question — but wrong for "hey".

This rail runs before the LLM and catches *pure* small talk only: a message whose
every token is a greeting/pleasantry/filler word. Anything with real content —
"hey, can I eat biryani?" — keeps a substantive token after filtering and falls
through untouched to the normal safety spine. The match is intentionally narrow so
it can never swallow a clinical question.

Owner: Ruthwik (scope ``brain``).
"""

from __future__ import annotations

import re

# Greeting / acknowledgement openers — at least one of these must be present for a
# message to count as small talk (so a string of pure filler words doesn't match).
_CORE: frozenset[str] = frozenset(
    {
        "hi", "hii", "hey", "heya", "hye", "hello", "helo", "hiya", "yo", "sup",
        "hola", "namaste", "greetings", "howdy",
        "morning", "afternoon", "evening",  # "good morning" etc.
        "thanks", "thank", "thankyou", "thx", "ty", "tysm",
        "bye", "goodbye", "cya", "ok", "okay", "okey", "k", "kk",
        "cool", "great", "nice", "awesome", "perfect", "welcome",
    }
)

# Filler that may accompany an opener without making it a real question.
_FILLER: frozenset[str] = frozenset(
    {
        "good", "morning", "afternoon", "evening", "night", "day",
        "how", "are", "is", "you", "u", "ya", "doing", "there", "again",
        "well", "fine", "very", "much", "so", "the", "a", "an", "my", "me",
        "i", "im", "your", "to", "for", "and", "please", "pls",
        "doctor", "doc", "dr", "sir", "madam", "maam", "team", "everyone",
        "hope", "all", "good", "see", "soon", "later", "ok", "just", "checking",
    }
)

_TOKEN = re.compile(r"[a-z']+")


def is_small_talk(question: str) -> bool:
    """True only if the whole message is a greeting/pleasantry (no clinical content).

    Empty / punctuation-only input also counts (there is nothing to answer). A
    message with any token outside the greeting+filler vocabulary is *not* small
    talk and is left for the normal spine.
    """
    tokens = _TOKEN.findall(question.lower())
    if not tokens:
        return True  # empty or punctuation-only — nudge, don't escalate
    if any(t not in _CORE and t not in _FILLER for t in tokens):
        return False  # a real word slipped in — treat as a genuine question
    return any(t in _CORE for t in tokens)  # require at least one greeting opener


__all__ = ["is_small_talk"]
