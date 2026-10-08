"""Deterministic answer-text grounding (gate-chain final invariant, policy v7).

The v6 citation veto checks citation *ids* only. The final red team showed the
gap: a confident reasoner said "Take Metformin 1000mg twice daily." — the
SUPERSEDED dose — while citing the CURRENT fact's id (500mg), an affirming LLM
verifier agreed, and the Brain and the graph both ANSWERED. Nothing
deterministic tied the answer *text* to the cited fact.

This module is that tie. Every **claim-bearing token** of the answer text must
appear in the text of at least one CITED fact of the current valid slice:

* **quantities** — a number with a unit: dose / strength (``1000mg``,
  ``500 mg``, ``500 milligrams``, ``0.5 ml``), duration (``2 weeks``,
  ``5 days``), interval (``every 8 hours``) and count (``2 tablets``);
* **frequencies** — ``once daily`` / ``twice`` / ``thrice`` / ``3 times`` /
  ``three times`` / ``2x`` and the Latin shorthands (``od bd tds qid`` …);
* **bare numbers** — any other digit run (dates, ages, readings), except the
  emergency numbers 112 / 108 / 102 / 911 / 999;
* **drug names** — a curated lexicon, drug-class suffix families
  (``…cillin``, ``…floxacin``, ``…prazole``, ``…statin`` …) and the names of
  this patient's own medication facts (current AND retired, so a stopped drug
  is recognised as a drug even when no lexicon knows it).

Normalisation is deliberately small and symmetric (applied to the answer and
to the fact): case, spacing between number and unit, thousands commas,
trailing decimal zeros, unit spellings (``milligrams`` → ``mg``), number words
before a unit (``two weeks`` → ``2 week``) and frequency words (``twice`` →
``2 times``). At v7 nothing was converted between units; v8 compares mass and
volume by value (below). Bare small number words are ignored ("one of your
medicines" is not a claim): the check is conservative — an unmatched token
never ANSWERS.

**v8 hardening** (final evaluator red team): compound number words
(``one thousand``, ``five hundred``, ``half a gram``, ``a gram``) are read as
numbers; mass and volume are compared by VALUE (``1 g`` == ``1000 mg``,
``500 mcg`` == ``0.5 mg``, ``1 l`` == ``1000 ml``) so a converted superseded
dose is caught; a count word next to a drug (``two Metformin tablets``) is a
count claim; dose-change words (``double``, ``twice the dose``, ``triple``,
``half``, ``increase``, ``extra``, ``more than prescribed``) are claims that a
cited fact must also make; common brands map to their generic before matching
(``Coumadin`` -> warfarin); any other word sitting right next to a dose
(``Zyxor 500mg``) is treated as a drug claim; and :func:`polarity_conflicts`
refuses an answer whose take/stop direction for a drug contradicts the cited
facts (or a non-current fact, when no cited fact states the answer's
direction).

Pure, keyless, deterministic. Owner: Priyanshu (scope ``safety``).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from careline.domain.enums import FactKind
from careline.domain.model.fact import Fact
from careline.domain.model.patient import Patient

# -- vocabulary ---------------------------------------------------------------

#: Unit spellings -> canonical unit. A number followed by one of these is a
#: quantity claim and must match the SAME (number, unit) in a cited fact.
UNIT_CANONICAL: dict[str, str] = {
    **dict.fromkeys(("mg", "mgs", "milligram", "milligrams", "milligramme", "milligrammes"), "mg"),
    **dict.fromkeys(("mcg", "ug", "microgram", "micrograms"), "mcg"),
    **dict.fromkeys(("g", "gm", "gms", "gram", "grams", "gramme", "grammes"), "g"),
    **dict.fromkeys(("kg", "kgs", "kilogram", "kilograms"), "kg"),
    **dict.fromkeys(("ml", "mls", "millilitre", "millilitres", "milliliter", "milliliters", "cc"), "ml"),
    **dict.fromkeys(("l", "litre", "litres", "liter", "liters"), "l"),
    **dict.fromkeys(("iu", "unit", "units"), "unit"),
    **dict.fromkeys(("tablet", "tablets", "tab", "tabs", "pill", "pills", "capsule", "capsules",
                     "cap", "caps"), "tablet"),
    **dict.fromkeys(("drop", "drops"), "drop"),
    **dict.fromkeys(("puff", "puffs"), "puff"),
    **dict.fromkeys(("teaspoon", "teaspoons", "tsp", "tablespoon", "tablespoons", "tbsp",
                     "spoon", "spoons", "spoonful", "spoonfuls"), "spoon"),
    **dict.fromkeys(("time", "times", "x"), "times"),
    **dict.fromkeys(("sec", "secs", "seconds"), "second"),
    **dict.fromkeys(("min", "mins", "minute", "minutes"), "minute"),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours"), "hour"),
    **dict.fromkeys(("day", "days"), "day"),
    **dict.fromkeys(("wk", "wks", "week", "weeks"), "week"),
    **dict.fromkeys(("month", "months"), "month"),
    **dict.fromkeys(("yr", "yrs", "year", "years"), "year"),
    **dict.fromkeys(("%", "percent", "per cent"), "percent"),
}

#: Number words. A small number word is read only when a unit follows
#: ("two weeks"), never bare ("one of your medicines"); a compound with
#: ``hundred`` / ``thousand`` ("one thousand") is a claim even without a unit.
NUMBER_WORDS: dict[str, str] = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
    "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
}
_SCALE_WORDS: dict[str, int] = {"hundred": 100, "thousand": 1000}

#: Value conversions: compared by value in one canonical unit.
_UNIT_SCALE: dict[str, tuple[str, Decimal]] = {
    "g": ("mg", Decimal(1000)),
    "mcg": ("mg", Decimal("0.001")),
    "l": ("ml", Decimal(1000)),
}
#: Units a bare "a"/"an" counts as one of ("a gram"), and the units whose
#: neighbouring word is read as a drug claim ("Zyxor 500mg").
_DOSE_UNITS = frozenset({"mg", "mcg", "g", "ml", "l", "unit"})
#: Count units a number word may reach across one drug word ("two Metformin tablets").
_COUNT_UNITS = frozenset({"tablet", "drop", "puff", "spoon"})

#: Dose-change words -> claim token. An ANSWER may only use one if a cited
#: fact uses the same one ("double your dose" is never inferred).
CHANGE_WORDS: dict[str, str] = {
    **dict.fromkeys(("double", "doubled", "doubles", "doubling"), "double"),
    **dict.fromkeys(("triple", "tripled", "triples", "tripling"), "triple"),
    **dict.fromkeys(("quadruple", "quadrupled", "quadrupling"), "multiply"),
    **dict.fromkeys(("half", "halve", "halved", "halves", "halving"), "half"),
    **dict.fromkeys(("increase", "increased", "increases", "increasing"), "increase"),
    **dict.fromkeys(("extra", "additional"), "extra"),
}
_DOSE_NOUNS = frozenset({"dose", "doses", "dosage", "amount", "usual", "normal", "prescribed"})
_POSSESSIVE = frozenset({"the", "your", "my", "his", "her", "their", "that", "this"})
_MORE_THAN = frozenset({"prescribed", "recommended", "directed", "advised", "instructed",
                        "told", "your", "the", "usual", "normal"})

#: Frequency words / shorthands -> "N times".
FREQUENCY_WORDS: dict[str, str] = {
    "twice": "2", "thrice": "3",
    "od": "1", "qd": "1", "bd": "2", "bid": "2", "tds": "3", "tid": "3", "qid": "4", "qds": "4",
}
# "once" is a frequency only in a dosing frame ("once daily", "once at night"),
# never in "once the wound heals".
_ONCE_FRAME = frozenset({
    "daily", "day", "a", "per", "every", "each", "weekly", "week", "nightly", "night",
    "at", "in", "morning", "evening", "bedtime", "only",
})

#: Emergency numbers are instructions, not clinical claims.
EXEMPT_NUMBERS: frozenset[str] = frozenset({"112", "108", "102", "911", "999"})

#: Curated drug lexicon (generic names common in Indian / UK / US practice).
DRUG_LEXICON: frozenset[str] = frozenset({
    "paracetamol", "ibuprofen", "aspirin", "diclofenac", "naproxen", "tramadol", "morphine",
    "codeine", "oxycodone", "amoxicillin", "amoxiclav", "augmentin", "azithromycin",
    "ciprofloxacin", "doxycycline", "metronidazole", "cefixime", "ceftriaxone", "cephalexin",
    "cefuroxime", "penicillin", "clindamycin", "nitrofurantoin", "metformin", "glimepiride",
    "gliclazide", "insulin", "sitagliptin", "atorvastatin", "rosuvastatin", "simvastatin",
    "amlodipine", "telmisartan", "losartan", "lisinopril", "ramipril", "enalapril", "metoprolol",
    "atenolol", "propranolol", "bisoprolol", "furosemide", "hydrochlorothiazide",
    "spironolactone", "warfarin", "heparin", "enoxaparin", "clopidogrel", "apixaban",
    "rivaroxaban", "dabigatran", "omeprazole", "pantoprazole", "esomeprazole", "rabeprazole",
    "ranitidine", "famotidine", "ondansetron", "domperidone", "metoclopramide",
    "levothyroxine", "prednisolone", "prednisone", "dexamethasone", "hydrocortisone",
    "salbutamol", "montelukast", "budesonide", "cetirizine", "levocetirizine", "loratadine",
    "fexofenadine", "diphenhydramine", "chlorpheniramine", "sertraline", "fluoxetine",
    "escitalopram", "citalopram", "amitriptyline", "alprazolam", "lorazepam", "diazepam",
    "clonazepam", "zolpidem", "gabapentin", "pregabalin", "levetiracetam", "phenytoin",
    "carbamazepine", "valproate", "lithium", "haloperidol", "olanzapine", "quetiapine",
    "risperidone", "digoxin", "nitroglycerin", "isosorbide", "allopurinol", "colchicine",
    "methotrexate", "hydroxychloroquine", "ferrous", "crocin", "dolo", "calpol", "combiflam",
    "disprin", "ecosprin", "lactulose", "loperamide", "acyclovir", "oseltamivir",
    "fluconazole", "mupirocin", "chlorhexidine", "povidone",
})

#: Synonyms read as the same drug on both sides.
DRUG_SYNONYMS: dict[str, str] = {
    "acetaminophen": "paracetamol",
    "albuterol": "salbutamol",
    "thyroxine": "levothyroxine",
    # v8: common brand -> generic (India / UK / US). A brand is read as its
    # generic on both sides, so "Coumadin" in an answer is "warfarin".
    **dict.fromkeys(("crocin", "dolo", "calpol", "tylenol", "panadol", "metacin", "pacimol",
                     "febrex"), "paracetamol"),
    **dict.fromkeys(("coumadin", "jantoven", "uniwarfin"), "warfarin"),
    **dict.fromkeys(("glucophage", "glycomet", "obimet", "gluconorm", "riomet"), "metformin"),
    **dict.fromkeys(("augmentin", "amoxil", "novamox", "clavam", "amoxiclav"),
                    "amoxicillin"),
    **dict.fromkeys(("ecosprin", "disprin", "loprin"), "aspirin"),
    **dict.fromkeys(("lasix", "frusemide", "frusenex"), "furosemide"),
    **dict.fromkeys(("lipitor", "atorva", "storvas", "tonact"), "atorvastatin"),
    **dict.fromkeys(("crestor", "rosuvas", "rozavel"), "rosuvastatin"),
    **dict.fromkeys(("brufen", "advil", "motrin", "nurofen", "ibugesic"), "ibuprofen"),
    **dict.fromkeys(("voveran", "voltaren", "voltarol"), "diclofenac"),
    **dict.fromkeys(("eliquis",), "apixaban"),
    **dict.fromkeys(("xarelto",), "rivaroxaban"),
    **dict.fromkeys(("pradaxa",), "dabigatran"),
    **dict.fromkeys(("plavix", "clopilet", "deplatt"), "clopidogrel"),
    **dict.fromkeys(("norvasc", "amlong", "stamlo", "amlodac"), "amlodipine"),
    **dict.fromkeys(("telma", "micardis"), "telmisartan"),
    **dict.fromkeys(("losar", "cozaar", "repace"), "losartan"),
    **dict.fromkeys(("zestril", "prinivil"), "lisinopril"),
    **dict.fromkeys(("lopressor", "toprol", "metolar"), "metoprolol"),
    **dict.fromkeys(("tenormin",), "atenolol"),
    **dict.fromkeys(("prilosec", "omez"), "omeprazole"),
    **dict.fromkeys(("protonix", "pantocid"), "pantoprazole"),
    **dict.fromkeys(("nexium",), "esomeprazole"),
    **dict.fromkeys(("zofran", "emeset", "vomikind"), "ondansetron"),
    **dict.fromkeys(("thyronorm", "eltroxin", "synthroid", "levoxyl"), "levothyroxine"),
    **dict.fromkeys(("asthalin", "ventolin"), "salbutamol"),
    **dict.fromkeys(("zithromax", "azithral", "azee"), "azithromycin"),
    **dict.fromkeys(("cipro", "ciplox"), "ciprofloxacin"),
    **dict.fromkeys(("flagyl", "metrogyl"), "metronidazole"),
    **dict.fromkeys(("taxim",), "cefixime"),
    **dict.fromkeys(("zoloft",), "sertraline"),
    **dict.fromkeys(("prozac",), "fluoxetine"),
    **dict.fromkeys(("lexapro", "nexito"), "escitalopram"),
    **dict.fromkeys(("xanax", "alprax"), "alprazolam"),
    **dict.fromkeys(("ativan",), "lorazepam"),
    **dict.fromkeys(("valium",), "diazepam"),
    **dict.fromkeys(("lyrica",), "pregabalin"),
    **dict.fromkeys(("neurontin",), "gabapentin"),
    **dict.fromkeys(("lantus", "humalog", "novorapid", "mixtard", "actrapid"), "insulin"),
    **dict.fromkeys(("januvia", "istavel"), "sitagliptin"),
    **dict.fromkeys(("amaryl",), "glimepiride"),
    **dict.fromkeys(("zyrtec", "cetzine", "okacet"), "cetirizine"),
    **dict.fromkeys(("allegra",), "fexofenadine"),
    **dict.fromkeys(("benadryl",), "diphenhydramine"),
    **dict.fromkeys(("wysolone", "omnacortil"), "prednisolone"),
    **dict.fromkeys(("zyloric",), "allopurinol"),
    **dict.fromkeys(("dilantin", "eptoin"), "phenytoin"),
    **dict.fromkeys(("tegretol",), "carbamazepine"),
    **dict.fromkeys(("keppra", "levipil"), "levetiracetam"),
    **dict.fromkeys(("lanoxin",), "digoxin"),
    **dict.fromkeys(("zantac", "aciloc", "rantac"), "ranitidine"),
    **dict.fromkeys(("imodium",), "loperamide"),
    **dict.fromkeys(("tamiflu",), "oseltamivir"),
    **dict.fromkeys(("zovirax",), "acyclovir"),
}

#: Drug-class suffix families (word >= 7 letters). Chosen so no everyday
#: English word ends with one ("…sterol" is deliberately absent).
DRUG_SUFFIXES: tuple[str, ...] = (
    "cillin", "mycin", "floxacin", "cycline", "prazole", "conazole", "nidazole", "tidine",
    "olol", "pril", "sartan", "dipine", "statin", "formin", "gliptin", "glitazone", "semide",
    "thiazide", "parin", "xaban", "gatran", "oxetine", "triptyline", "zepam", "zolam",
    "codone", "profen", "fenac", "coxib", "lukast", "triptan", "setron", "peridol",
    "gabalin", "pentin", "olone", "isone", "asone", "apine", "epine", "ovir", "avir",
)

#: Words inside a medication name that are forms, not the drug.
_FORM_WORDS = frozenset({
    "tablet", "tablets", "capsule", "capsules", "syrup", "injection", "cream", "ointment",
    "drops", "extended", "release", "oral", "solution", "suspension", "vitamin", "sodium",
    "hydrochloride", "with", "and", "plus", "forte", "gel", "spray", "inhaler", "patch",
})

_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-z]+|%")
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")


# -- normalisation -------------------------------------------------------------


def _norm_number(raw: str) -> list[str]:
    """'1,000' -> ['1000']; '0.50' -> ['0.5']; '2,5' -> ['2', '5'] (not a decimal)."""
    if _THOUSANDS.fullmatch(raw):
        raw = raw.replace(",", "")
    out = []
    for part in raw.split(","):
        if "." in part:
            whole, _, frac = part.partition(".")
            frac = frac.rstrip("0")
            part = f"{int(whole or '0')}.{frac}" if frac else str(int(whole or "0"))
        else:
            part = str(int(part))
        out.append(part)
    return out


def _canon_drug(word: str) -> str:
    word = DRUG_SYNONYMS.get(word, word)
    return word


def _is_drug(word: str, extra: frozenset[str]) -> str | None:
    """Canonical drug name if ``word`` names a drug, else None."""
    for cand in (word, word[:-1] if word.endswith("s") and len(word) > 4 else None):
        if not cand:
            continue
        canon = _canon_drug(cand)
        if canon in DRUG_LEXICON or canon in extra:
            return canon
        if len(cand) >= 7 and cand.endswith(DRUG_SUFFIXES):
            return canon
    return None


#: Words that commonly sit right next to a dose and are NOT a drug name.
#: Any other word of >= 4 letters directly before a dose ("Zyxor 500mg") or
#: right after "<dose> of" ("500mg of Zyxor") is read as a drug claim.
_DOSE_NEIGHBOUR_STOP = frozenset({
    "take", "takes", "taking", "took", "given", "give", "giving", "use", "using", "used",
    "continue", "continuing", "keep", "keeping", "start", "starting", "resume", "restart",
    "your", "their", "this", "that", "these", "those", "them", "with", "without", "plus",
    "then", "also", "only", "just", "about", "around", "approximately", "exactly", "least",
    "most", "than", "upto", "under", "over", "below", "above", "between", "within",
    "dose", "doses", "dosage", "strength", "total", "maximum", "minimum", "daily", "each",
    "every", "once", "twice", "thrice", "times", "usual", "normal", "same", "full", "single",
    "first", "second", "third", "next", "last", "morning", "evening", "night", "nightly",
    "bedtime", "noon", "afternoon", "prescribed", "prescribing", "prescription",
    "current", "currently", "now", "still", "instead", "another", "remaining", "more",
    "less", "much", "many", "have", "having", "need", "needs", "should", "will", "would",
    "could", "must", "shall", "were", "been", "being", "into", "onto", "from", "after",
    "before", "until", "till", "while", "when", "where", "which", "what", "medicine",
    "medicines", "medication", "medications", "drug", "drugs", "tablet", "tablets", "pill",
    "pills", "capsule", "capsules", "syrup", "injection", "injections", "liquid", "water",
    "milk", "juice", "food", "meal", "meals", "glass", "cup", "spoon", "teaspoon", "dissolved",
    "mixed", "contains", "containing", "reduced", "lowered", "changed", "switched",
    "doctor", "doctors", "advised", "says", "said", "per", "please", "make", "sure",
    "already", "today", "tonight", "tomorrow", "week", "weeks", "days", "hours", "dosed",
})


def _dec(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation:  # pragma: no cover - _norm_number output is numeric
        return Decimal(0)


def _fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _quantity(value: Decimal, unit: str) -> str:
    """'1 g' -> '1000 mg'; '500 mcg' -> '0.5 mg'; '1 l' -> '1000 ml'."""
    if unit in _UNIT_SCALE:
        unit, factor = _UNIT_SCALE[unit]
        value = value * factor
    return f"{_fmt(value)} {unit}"


def _number_words(words: list[str], i: int) -> tuple[Decimal, int, bool] | None:
    """Parse a number-word phrase at ``i``: (value, next index, has a scale word).

    "one thousand" -> 1000; "two hundred and fifty" -> 250; "thousand" -> 1000;
    "a thousand" -> 1000; "a gram" -> 1 (only before a dose unit);
    "half a gram" / "half an hour" -> 0.5. None if no number phrase starts here.
    """
    n = len(words)
    w = words[i]
    nxt = words[i + 1] if i + 1 < n else ""
    if w == "half" and nxt in ("a", "an") and i + 2 < n and words[i + 2] in UNIT_CANONICAL:
        return Decimal("0.5"), i + 2, False
    if w in ("a", "an"):
        if nxt in _SCALE_WORDS:
            i += 1  # "a thousand" == "one thousand"
        elif UNIT_CANONICAL.get(nxt) in _DOSE_UNITS:
            return Decimal(1), i + 1, False
        else:
            return None
    elif w not in NUMBER_WORDS and w not in _SCALE_WORDS:
        return None
    total, current, big, k, seen = 0, 0, False, i, False
    while k < n:
        t = words[k]
        if t in NUMBER_WORDS:
            current += int(NUMBER_WORDS[t])
        elif t in _SCALE_WORDS:
            big = True
            if _SCALE_WORDS[t] == 1000:
                total += (current or 1) * 1000
                current = 0
            else:
                current = (current or 1) * 100
        elif (t == "and" and seen and k + 1 < n
              and (words[k + 1] in NUMBER_WORDS or words[k + 1] in _SCALE_WORDS)):
            pass
        else:
            break
        seen = True
        k += 1
    return Decimal(total + current), k, big


def _neighbour_drug(word: str, extra: frozenset[str]) -> str | None:
    """A word right next to a dose that may name an (unknown) drug."""
    if (len(word) < 4 or not word.isalpha() or word in _DOSE_NEIGHBOUR_STOP
            or word in NUMBER_WORDS or word in _SCALE_WORDS or word in UNIT_CANONICAL
            or word in CHANGE_WORDS or word in FREQUENCY_WORDS):
        return None
    if _is_drug(word, extra) is not None:
        return None  # emitted by the ordinary drug branch
    return word


def _scan(text: str, extra_drugs: frozenset[str]) -> tuple[list[str], frozenset[str]]:
    """(claim tokens in order, every plain word) for ``text``."""
    words = _TOKEN.findall((text or "").lower().replace("µg", "mcg").replace("μg", "mcg")
                           .replace("per cent", "percent"))
    n = len(words)
    tokens: list[str] = []

    def at(k: int) -> str:
        return words[k] if 0 <= k < n else ""

    def emit_quantity(value: Decimal, unit: str, start: int, j: int) -> int:
        """Emit '<value> <unit>' for the number at ``start`` whose unit is at
        ``j``; returns the next index. Handles 'N times the dose' (a change
        claim) and the drug-neighbour rule for dose units."""
        if unit == "times" and at(j + 1) in _POSSESSIVE and (
            at(j + 2) in _DOSE_NOUNS or at(j + 3) in _DOSE_NOUNS
        ):
            tokens.append("change:" + {"2": "double", "3": "triple"}.get(_fmt(value), "multiply"))
            return j + 1
        tokens.append(_quantity(value, unit))
        if unit in _DOSE_UNITS:
            for cand in (at(start - 1), at(j + 2) if at(j + 1) == "of" else ""):
                name = _neighbour_drug(cand, extra_drugs) if cand else None
                if name is not None:
                    tokens.append(f"drug:{name}")
        return j + 1

    i = 0
    while i < n:
        w = words[i]
        nxt = at(i + 1)
        if w[0].isdigit() or w == "%":
            if w == "%":
                i += 1
                continue
            nums = _norm_number(w)
            for num in nums[:-1]:
                tokens.append(num)
            value, j = _dec(nums[-1]), i + 1
            if at(j) == "and" and at(j + 1) == "a" and at(j + 2) == "half":
                value, j = value + Decimal("0.5"), j + 3
            unit = UNIT_CANONICAL.get(at(j))
            if unit is not None:
                i = emit_quantity(value, unit, i, j)
                continue
            drug = _is_drug(at(j), extra_drugs) if at(j) else None
            if drug is not None and UNIT_CANONICAL.get(at(j + 1)) in _COUNT_UNITS:
                tokens.append(f"drug:{drug}")
                tokens.append(_quantity(value, UNIT_CANONICAL[at(j + 1)]))
                i = j + 2
                continue
            tokens.append(nums[-1])
            i += 1
            continue
        if w == "twice" and nxt in _POSSESSIVE and (
            at(i + 2) in _DOSE_NOUNS or at(i + 3) in _DOSE_NOUNS
        ):
            tokens.append("change:double")
            i += 1
            continue
        parsed = _number_words(words, i)
        if parsed is not None:
            value, j, big = parsed
            if at(j) == "and" and at(j + 1) == "a" and at(j + 2) == "half":
                value, j = value + Decimal("0.5"), j + 3
            unit = UNIT_CANONICAL.get(at(j))
            if unit is not None:
                i = emit_quantity(value, unit, i, j)
                continue
            drug = _is_drug(at(j), extra_drugs) if at(j) else None
            if drug is not None and UNIT_CANONICAL.get(at(j + 1)) in _COUNT_UNITS:
                tokens.append(f"drug:{drug}")
                tokens.append(_quantity(value, UNIT_CANONICAL[at(j + 1)]))
                i = j + 2
                continue
            if big:
                # "one thousand" with no unit is still a number claim.
                tokens.append(_fmt(value))
                i = j
                continue
            if w not in ("a", "an", "half"):
                i = j  # a bare small number word is not a claim ("one of them")
                continue
        if w in FREQUENCY_WORDS:
            tokens.append(f"{FREQUENCY_WORDS[w]} times")
        elif w == "once" and nxt in _ONCE_FRAME:
            tokens.append("1 times")
        elif w in CHANGE_WORDS and not (w.startswith("doubl") and nxt.startswith("check")):
            tokens.append(f"change:{CHANGE_WORDS[w]}")
        elif w == "more" and nxt == "than" and at(i + 2) in _MORE_THAN and (
            at(i + 2) not in _POSSESSIVE or at(i + 3) in _DOSE_NOUNS
        ):
            tokens.append("change:more")
        else:
            drug = _is_drug(w, extra_drugs)
            if drug is not None:
                tokens.append(f"drug:{drug}")
        i += 1
    plain = frozenset(_canon_drug(w) for w in words if w.isalpha())
    return tokens, plain


def answer_tokens(text: str, extra_drugs: Iterable[str] = ()) -> list[str]:
    """The claim-bearing tokens of ``text`` (ordered, de-duplicated)."""
    tokens, _ = _scan(text, frozenset(extra_drugs))
    seen: dict[str, None] = {}
    for t in tokens:
        if t not in EXEMPT_NUMBERS:
            seen.setdefault(t, None)
    return list(seen)


def _render(value: object) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f"{value.day} {value:%B} {value:%b} {value.year} {value.isoformat()}"
    return str(value)


_NON_TEXT_FIELDS = frozenset({"id", "kind", "validity", "approved_by", "approved_at"})


def fact_text(fact: Fact) -> str:
    """Every doctor-authored text field of ``fact`` (summary + structured fields)."""
    parts = [fact.summary]
    for name, value in fact:
        if name in _NON_TEXT_FIELDS or name == "summary" or value is None:
            continue
        parts.append(_render(value))
    return " . ".join(parts)


def medication_names(facts: Iterable[Fact]) -> frozenset[str]:
    """Drug words named by medication facts (current or retired)."""
    out: set[str] = set()
    for f in facts:
        if f.kind is FactKind.MEDICATION:
            for w in re.findall(r"[a-z]+", str(getattr(f, "name", "")).lower()):
                if len(w) >= 4 and w not in _FORM_WORDS:
                    out.add(_canon_drug(w))
    return frozenset(out)


def _grounded_in(token: str, fact_tokens: set[str], fact_words: frozenset[str]) -> bool:
    if token.startswith("drug:"):
        name = token[5:]
        return token in fact_tokens or name in fact_words
    return token in fact_tokens or (" " not in token and any(
        t == token or t.split(" ")[0] == token for t in fact_tokens
    ))


def _pool(facts: Iterable[Fact], extra: frozenset[str]) -> tuple[set[str], frozenset[str]]:
    tokens: set[str] = set()
    words: set[str] = set()
    for f in facts:
        t, w = _scan(fact_text(f), extra)
        tokens.update(t)
        words.update(w)
    return tokens, frozenset(words)


def ungrounded_tokens(
    answer: str, cited: Iterable[Fact], extra_drugs: Iterable[str] = ()
) -> list[str]:
    """Tokens of ``answer`` that appear in no fact of ``cited`` (ordered).

    A quantity must match the same (number, unit); a bare number matches the
    number with or without a unit; a drug matches by canonical name.
    """
    cited = list(cited)
    extra = frozenset(extra_drugs) | medication_names(cited)
    fact_tokens, fact_words = _pool(cited, extra)
    return [
        t for t in answer_tokens(answer, extra)
        if not _grounded_in(t, fact_tokens, fact_words)
    ]


def facts_containing(token: str, facts: Iterable[Fact], extra_drugs: Iterable[str] = ()) -> list[str]:
    """Ids of ``facts`` whose text contains ``token`` (for the trace)."""
    extra = frozenset(extra_drugs)
    out = []
    for f in facts:
        t, w = _pool([f], extra)
        if _grounded_in(token, t, w):
            out.append(f.id)
    return out


# -- take / stop polarity (v8) -------------------------------------------------

_STOP_CUES = frozenset({
    "stop", "stops", "stopped", "stopping", "discontinue", "discontinued", "discontinuing",
    "cease", "ceased", "quit", "avoid", "avoids", "avoided", "avoiding", "withhold",
    "withheld", "hold", "halt", "halted", "suspend", "suspended", "cancel", "cancelled",
    "skip", "omit",
})
_TAKE_CUES = frozenset({
    "take", "takes", "taking", "took", "continue", "continued", "continues", "continuing",
    "keep", "resume", "resumed", "resuming", "restart", "restarted", "start", "started",
    "starting", "use", "using", "prescribed", "prescribing", "prescribe", "carry",
})
_NEGATIONS = frozenset({"not", "never", "no", "t", "cannot", "dont", "nor"})
#: Words a negation reaches across ("do not ever take", "should not be taking").
_NEG_FILLER = frozenset({
    "ever", "to", "be", "you", "it", "any", "longer", "more", "again", "still", "need",
    "should", "must", "can", "do", "does", "please", "further", "yet",
})
_INSTEAD = (("instead", "of"), ("rather", "than"))
#: After a negated take verb these make it a LIMIT ("do not take over 4 a day").
_LIMIT_WORDS = frozenset({"over", "beyond", "above", "exceeding"})
#: A drug mention that names OTHER products, not a direction about this drug.
_OTHER_PRODUCT = frozenset({"other", "another", "containing"})
_PRODUCT_SUFFIX = frozenset({"containing", "based", "products", "product", "combination",
                             "combinations"})
_CLAUSE_SPLIT = re.compile(r"(?<!\d)\.(?!\d)|[;!?\n\u2014]|\bbut\b|\bhowever\b|\bwhereas\b")
_WORD = re.compile(r"[a-z]+")


def _clause_polarity(clause: str, extra: frozenset[str], dose_in_clause: bool) -> dict[str, set[str]]:
    words = _WORD.findall(clause)
    out: dict[str, set[str]] = {}
    mode: str | None = None
    neg = False
    after_stop = False
    pending: list[str] = []  # drugs seen before any cue in this clause
    k = 0
    while k < len(words):
        w = words[k]
        if (w, words[k + 1] if k + 1 < len(words) else "") in _INSTEAD:
            mode, neg, after_stop = "stop", False, False
            k += 2
            continue
        if w in _NEGATIONS:
            neg = True
        elif w in _STOP_CUES:
            mode = "take" if neg else "stop"
            neg, after_stop = False, mode == "stop"
        elif w in _TAKE_CUES:
            if not after_stop:
                nxt = words[k + 1:k + 3]
                limit = nxt[:2] == ["more", "than"] or (nxt[:1] and nxt[0] in _LIMIT_WORDS)
                # "do not take more than 3 a day" is a dose LIMIT, not a stop.
                mode = "stop" if neg and not limit else "take"
            neg = False
        else:
            drug = _is_drug(w, extra)
            prev = words[k - 1] if k > 0 else ""
            nxt_w = words[k + 1] if k + 1 < len(words) else ""
            if drug is not None and (prev in _OTHER_PRODUCT or nxt_w in _PRODUCT_SUFFIX):
                drug = None  # "other paracetamol-containing products": not this drug
            if drug is not None:
                if mode is None:
                    pending.append(drug)
                else:
                    out.setdefault(drug, set()).add(mode)
            if w not in _NEG_FILLER:
                neg = False
            after_stop = False
        if mode is not None and pending:
            for d in pending:
                out.setdefault(d, set()).add(mode)
            pending = []
        k += 1
    if pending and dose_in_clause:
        for d in pending:  # "Metformin 500mg twice daily." is an instruction to take
            out.setdefault(d, set()).add("take")
    return out


def drug_polarity(text: str, extra_drugs: Iterable[str] = ()) -> dict[str, set[str]]:
    """{canonical drug: {"take", "stop"}} as the text directs, clause by clause.

    The cue nearest before a drug in its clause sets the direction ("do not
    take X" -> stop; "do not stop X" -> take; "X instead of Y" -> Y stop);
    with no cue before it, the first cue after it in the clause ("warfarin
    stopped"); with no cue at all, a clause carrying a dose/frequency is an
    instruction to take; otherwise the drug is merely mentioned.
    """
    extra = frozenset(extra_drugs)
    out: dict[str, set[str]] = {}
    for clause in _CLAUSE_SPLIT.split((text or "").lower()):
        if not clause or not clause.strip():
            continue
        toks, _ = _scan(clause, extra)
        dose = any(not t.startswith(("drug:", "change:")) for t in toks)
        for d, pol in _clause_polarity(clause, extra, dose).items():
            out.setdefault(d, set()).update(pol)
    return out


def _facts_polarity(facts: Iterable[Fact], extra: frozenset[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in facts:
        for d, pol in drug_polarity(fact_text(f), extra).items():
            out.setdefault(d, set()).update(pol)
    return out


def polarity_conflicts(
    answer: str,
    cited: Iterable[Fact],
    non_current: Iterable[Fact] = (),
    extra_drugs: Iterable[str] = (),
) -> list[str]:
    """Drugs whose take/stop direction in ``answer`` is contradicted.

    For a drug the answer says to take (or to stop):

    * a CITED fact directs the opposite and no cited fact directs the same
      ("Take ibuprofen" while the cited fact says "Stop ibuprofen"); or
    * a NON-CURRENT fact directs the opposite and no cited fact directs the
      same — the only explicit direction on record is the other way.

    Returns human-readable conflict tokens (empty = consistent).
    """
    cited = list(cited)
    non_current = list(non_current)
    extra = frozenset(extra_drugs) | medication_names(cited) | medication_names(non_current)
    ans = drug_polarity(answer, extra)
    if not ans:
        return []
    cited_pol = _facts_polarity(cited, extra)
    old_pol = _facts_polarity(non_current, extra)
    out: list[str] = []
    for drug, pols in ans.items():
        for pol in sorted(pols):
            opp = "stop" if pol == "take" else "take"
            here = cited_pol.get(drug, set())
            if pol in here:
                continue
            if opp in here:
                out.append(f"polarity:{pol} {drug} (cited fact says {opp})")
            elif opp in old_pol.get(drug, set()):
                out.append(f"polarity:{pol} {drug} (non-current fact says {opp})")
    return out


def non_current_facts(patient: Patient, now: datetime) -> tuple[Fact, ...]:
    """This patient's facts that are NOT current at ``now`` — superseded, not
    yet valid, or unapproved. Never answer material; used only to recognise a
    retired drug name and to name the fact in the trace."""
    return tuple(f for f in patient.facts if not f.is_current(now))


__all__ = [
    "DRUG_LEXICON",
    "DRUG_SUFFIXES",
    "DRUG_SYNONYMS",
    "EXEMPT_NUMBERS",
    "FREQUENCY_WORDS",
    "NUMBER_WORDS",
    "UNIT_CANONICAL",
    "CHANGE_WORDS",
    "answer_tokens",
    "drug_polarity",
    "fact_text",
    "facts_containing",
    "medication_names",
    "non_current_facts",
    "polarity_conflicts",
    "ungrounded_tokens",
]
