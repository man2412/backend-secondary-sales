"""
Pure normalization helpers for the entity-import flow.

No DB or IO access. Every function in here takes a string (or None) and
returns a string. The single responsibility is collapsing the many
spelling/format variants of the same real-world entity (medical store,
doctor, stockist, city) down to a stable canonical form, so we can detect
duplicates without a fancy embedding service.

Public helpers:
    clean_whitespace(s)              → str
    normalize_store_name(s)          → str  (canonical match key; many tokens)
    normalize_store_core(s)          → str  (just the identifying part, city removed)
    normalize_doctor_name(s)         → str
    normalize_person_name(s)         → str  (FSO/RSM/ASM)
    normalize_stockist_name(s)       → (clean_name, bracketed_division_label)
    canonical_city_token(token)      → str
    canonical_city_from_location(s)  → str
    extract_city_from_store_raw(s)   → str
    pick_canonical_store_name(vs)    → str
    fuzzy_ratio(a, b)                → float
    is_fuzzy_match(a, b, threshold)  → bool
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# Whitespace / unicode hygiene
# ---------------------------------------------------------------------------

# NBSP and friends often appear when copy-pasting from Excel/Word.
_NBSP_AND_FRIENDS = re.compile(r"[\u00a0\u2000-\u200b\u202f\u205f\u3000]")
_MULTI_WS = re.compile(r"\s+")
_PUNCT_TO_SPACE = re.compile(r"[,\-_/.()'\":!?@#&*+\[\]]+")


def clean_whitespace(s: str | None) -> str:
    """NFKC-normalise, replace NBSP-family chars with regular space, collapse runs."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _NBSP_AND_FRIENDS.sub(" ", s)
    s = _MULTI_WS.sub(" ", s)
    return s.strip()


# ---------------------------------------------------------------------------
# City / area equivalence
# ---------------------------------------------------------------------------

# Word-level aliases — different spellings that mean the same place.
# Keep these *very* tight (only obvious variants) to avoid false positives.
# Add new entries as data shows new spellings; the import flow logs unseen
# city tokens so curating this list is straightforward.
_CITY_ALIASES: dict[str, str] = {
    # Padhari area (sheet showed PADDHARI / PADADHARI / PADHARI / PADADHRI)
    "paddhari": "padhari",
    "padadhari": "padhari",
    "padadhri": "padhari",
    "padhari": "padhari",
    # Satellite area
    "satellite": "satellite",
    "satelitte": "satellite",
    "setellite": "satellite",
    "setelite": "satellite",
    "satelite": "satellite",
    # Ahmedabad (the city) — incl. the truncated 'AHMEDAB' seen when an
    # Excel column gets clipped at width.
    "ahmedabad": "ahmedabad",
    "ahemdabad": "ahmedabad",
    "ahmadabad": "ahmedabad",
    "ahembedabad": "ahmedabad",
    "ahmedabaad": "ahmedabad",
    "ahmedab": "ahmedabad",
    "ahemdab": "ahmedabad",
    # A few common HQ tokens — keep canonical form
    "naroda": "naroda",
    "bapunagar": "bapunagar",
    "himatnagar": "himatnagar",
    "mehsana": "mehsana",
    "kadi": "kadi",
    "odhav": "odhav",
    "maninagar": "maninagar",
    "paldi": "paldi",
    "vejalpur": "vejalpur",
    "gota": "gota",
    "ghatlodiya": "ghatlodiya",
    "sabarmati": "sabarmati",
    "sardarnagar": "sardarnagar",
    "prahladnagar": "prahladnagar",
    "ambawadi": "ambawadi",
    "ambavadi": "ambawadi",
}


def canonical_city_token(token: str) -> str:
    """Lowercase letters-only form of a token, mapped through the city alias table."""
    if not token:
        return ""
    t = re.sub(r"[^a-z]", "", token.lower())
    return _CITY_ALIASES.get(t, t)


# ---------------------------------------------------------------------------
# Store-name normalization
# ---------------------------------------------------------------------------

# Generic words on a chemist label that don't carry identity.
_STORE_STOPWORDS: frozenset[str] = frozenset(
    {
        "medical", "medicals", "medicine", "medicines",
        "chemist", "chemists", "store", "stores",
        "pharma", "pharmacy", "pharmacies",
        "agency", "agencies", "distributor", "distributors",
        "and",
    }
)


def _strip_leading_codes(s: str) -> str:
    """
    Drop a leading distributor code that precedes ' - ' before the real name, e.g.
        '341S387 - SHYAM MEDICINES, SATELLITE'  → 'SHYAM MEDICINES, SATELLITE'
        'AMB27 - AMBICA MEDICAL STORE'         → 'AMBICA MEDICAL STORE'
    Recognises tokens that are ≥4 chars, alphanumeric, and contain at least one digit.

    Also strips the 'Party Name : ' prefix emitted by some billing-software exports:
        'Party Name : SHREE RANG PHARMACY NAVSARI, NAVSARI-'  → 'SHREE RANG PHARMACY NAVSARI, NAVSARI-'
    """
    s = re.sub(r"^Party\s+Name\s*:\s*", "", s, flags=re.IGNORECASE).strip()
    parts = s.split(" - ", 1)
    if len(parts) != 2:
        return s
    head, tail = parts[0].strip(), parts[1].strip()
    if (
        len(head) >= 4
        and any(ch.isdigit() for ch in head)
        and re.fullmatch(r"[A-Za-z0-9]+", head)
    ):
        return tail
    return s


def _strip_trailing_codes(s: str) -> str:
    """Drop trailing parenthetical codes '(0132)' / '(A00394)' and bare numeric tails '1051.'."""
    s = re.sub(r"\(\s*[A-Za-z]*\d+[A-Za-z0-9]*\s*\)\s*$", "", s).strip()
    s = re.sub(r"\b\d{3,}\.?\s*$", "", s).strip()
    return s


def _tokenize_clean_store(raw: str | None) -> list[str]:
    """Return the cleaned, lowercased, alias-canonicalized token list for a store name."""
    s = clean_whitespace(raw)
    if not s:
        return []
    s = _strip_leading_codes(s)
    s = _strip_trailing_codes(s)
    s = _PUNCT_TO_SPACE.sub(" ", s).lower()
    s = _MULTI_WS.sub(" ", s).strip()
    tokens = [canonical_city_token(t) for t in s.split() if t]
    return [t for t in tokens if t]


def normalize_store_name(raw: str | None) -> str:
    """
    Canonical *match key* for a chemist/store name.

    Strategy:
      - strip leading & trailing distributor codes
      - drop punctuation, lower-case
      - canonicalise city/area tokens (so 'padhari' / 'paddhari' / 'padadhari' collapse)
      - keep generic words like 'medical' / 'store' / 'chemist' because they
        *do* differentiate stores ('AMBICA CHEMIST' ≠ 'AMBICA MEDICAL STORE')
      - sort tokens so word-order variations collapse

    Returns the empty string if nothing identifying remains.
    """
    tokens = _tokenize_clean_store(raw)
    if not tokens:
        return ""
    return " ".join(sorted(set(tokens)))


def store_name_tokens(raw: str | None) -> frozenset[str]:
    """Set view of `normalize_store_name`, useful for subset / overlap comparisons."""
    tokens = _tokenize_clean_store(raw)
    return frozenset(tokens) if tokens else frozenset()


def normalize_store_core(raw: str | None) -> str:
    """
    Like `normalize_store_name` but *without* city/area tokens — the bare
    identifying brand portion of the name. Useful when two records reference
    the same brand from different listed cities.
    """
    tokens = _tokenize_clean_store(raw)
    out: list[str] = []
    for t in tokens:
        if t in _STORE_STOPWORDS:
            continue
        # drop tokens that match a known city alias (canonical city token)
        if _CITY_ALIASES.get(t) == t or t in _CITY_ALIASES.values():
            continue
        out.append(t)
    if not out:
        return ""
    return " ".join(sorted(set(out)))


def extract_city_from_store_raw(raw: str | None) -> str:
    """Best-effort: pick the trailing city/area token from a chemist name."""
    s = clean_whitespace(raw)
    if not s:
        return ""
    s = _strip_leading_codes(s)
    s = _strip_trailing_codes(s)
    parts = re.split(r"[,\-]", s)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return ""
    # try the last comma/dash chunk first, walk backwards through its tokens
    for chunk in reversed(parts):
        for tok in reversed(chunk.split()):
            ct = canonical_city_token(tok)
            if ct and ct not in _STORE_STOPWORDS:
                return ct
    return ""


def canonical_city_from_location(loc: str | None) -> str:
    """Pull the most likely city token out of a free-text location/address."""
    s = clean_whitespace(loc)
    if not s:
        return ""
    parts = re.split(r"[,\-]", s)
    for chunk in reversed(parts):
        for tok in reversed(chunk.split()):
            ct = canonical_city_token(tok)
            if ct and ct not in _STORE_STOPWORDS:
                return ct
    return ""


def pick_canonical_store_name(variants: list[str]) -> str:
    """
    Choose a 'clean' primary display name from a set of raw variants.

    Preference order:
      1. variants without a leading distributor code beat those that have one
      2. shorter strings beat longer ones (fewer city/qualifier tags)
      3. variants with fewer punctuation chars win the tie
    """
    cleaned = [clean_whitespace(v) for v in variants if v and v.strip()]
    if not cleaned:
        return ""

    def _rank(v: str) -> tuple[int, int, int]:
        has_leading_code = v != _strip_leading_codes(v)
        punct_count = sum(1 for ch in v if not (ch.isalnum() or ch.isspace()))
        return (1 if has_leading_code else 0, len(v), punct_count)

    cleaned.sort(key=_rank)
    return cleaned[0]


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


def fuzzy_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_fuzzy_match(a: str, b: str, *, threshold: float = 0.88) -> bool:
    """
    True iff `a` and `b` look like the same canonical key.

    Order of checks:
      1. exact equality
      2. one is contained in the other (length ≥ 4)
      3. SequenceMatcher ratio ≥ threshold
    """
    if a == b:
        return True
    if not a or not b:
        return False
    if len(a) >= 4 and a in b:
        return True
    if len(b) >= 4 and b in a:
        return True
    return fuzzy_ratio(a, b) >= threshold


# ---------------------------------------------------------------------------
# Person / doctor / stockist normalization
# ---------------------------------------------------------------------------


def normalize_doctor_name(raw: str | None) -> str:
    """
    Match key for a doctor.

    Strategy:
      - drop degree parentheticals like '(MD)', '(MBBS)'
      - upper-case and collapse whitespace, preserving everything else

    Returns '' for empty input.
    """
    s = clean_whitespace(raw)
    if not s:
        return ""
    s = re.sub(r"\([^)]*\)", " ", s)
    s = _MULTI_WS.sub(" ", s).strip().upper()
    return s


def normalize_person_name(raw: str | None) -> str:
    """Match key for FSO/RSM/ASM (Users): letters and spaces only, upper-case."""
    s = clean_whitespace(raw)
    if not s:
        return ""
    s = re.sub(r"[^A-Za-z ]", " ", s)
    s = _MULTI_WS.sub(" ", s).strip()
    return s.upper()


def normalize_stockist_name(raw: str | None) -> tuple[str, str]:
    """
    Strip the trailing bracketed division label like '[CD CARE]' from a
    stockist name. Returns (clean_name, division_label). Either may be ''.
    """
    s = clean_whitespace(raw)
    if not s:
        return ("", "")
    m = re.search(r"\[([^\]]+)\]\s*$", s)
    division_label = m.group(1).strip() if m else ""
    cleaned = re.sub(r"\[[^\]]+\]\s*$", "", s).strip()
    return (cleaned, division_label)


def normalize_stockist_key(raw: str | None) -> str:
    """Lower-case, punctuation-free match key for stockist deduplication."""
    name, _ = normalize_stockist_name(raw)
    if not name:
        return ""
    s = _PUNCT_TO_SPACE.sub(" ", name).lower()
    s = _MULTI_WS.sub(" ", s).strip()
    return s


def normalize_hq_key(raw: str | None) -> str:
    """Lower-case, punctuation-free match key for headquarter names."""
    s = clean_whitespace(raw)
    if not s:
        return ""
    s = _PUNCT_TO_SPACE.sub(" ", s).lower()
    s = _MULTI_WS.sub(" ", s).strip()
    return s
