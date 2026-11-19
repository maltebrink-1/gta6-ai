"""Small text helpers shared by the fact checker, captions and TTS."""
from __future__ import annotations

import re

# Product / title names whose digits are part of a name, not a factual number.
_NAME_PATTERNS = [
    r"grand theft auto(?::)?\s*(?:vi|v|iv|iii|ii|6|5|4|3|2)\b",
    r"\bgta\s*(?:vi|v|iv|iii|6|5|4|3)\b",
    r"\bplaystation\s*[2-5]\b",
    r"\bps\s?[2-5]\b",
    r"\bxbox series x\s*(?:\|\s*s|and s|/s)?\b",
    r"\bxbox one\b",
    r"\bxbox 360\b",
    r"\bred dead redemption\s*2\b",
    r"\brdr\s*2\b",
    r"\b4k\b",
]
_NAME_RE = re.compile("|".join(_NAME_PATTERNS), re.IGNORECASE)

# Structural numbers that are about the video itself, not about the game.
_STRUCTURAL_RE = re.compile(
    r"\b(?:number|no\.|#)\s*\d{1,2}\b"
    r"|\b\d{1,2}\s+(?:things|facts|reasons|details|secrets|tips|ways|changes|questions|moments)\b"
    r"|\b\d{1,3}\s+seconds?\b",
    re.IGNORECASE,
)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")

_ROMAN = {"vi": "6", "v": "5", "iv": "4", "iii": "3"}


def strip_names(text: str) -> str:
    """Remove game/console names so their digits are not treated as claims."""
    return _NAME_RE.sub(" ", text)


def _norm_number(raw: str) -> str:
    raw = raw.replace(",", "")
    if "." in raw:
        raw = raw.rstrip("0").rstrip(".") if re.fullmatch(r"\d+\.\d+", raw) else raw
    return raw


def extract_numbers(text: str, *, ignore_structural: bool = True) -> set[str]:
    """Return the set of normalised numbers in ``text`` (digits only).

    Game/console names (GTA 6, PS5, Red Dead Redemption 2 …) are ignored, and so
    are structural counts such as "5 things" or "number 1" when requested.
    """
    cleaned = strip_names(text)
    if ignore_structural:
        cleaned = _STRUCTURAL_RE.sub(" ", cleaned)
    out: set[str] = set()
    for m in _NUM_RE.finditer(cleaned):
        out.add(_norm_number(m.group(0)))
    return out


def allowed_numbers(texts: list[str]) -> set[str]:
    """Numbers a script line may use, given the texts of the facts it cites.

    Large integers also allow their rounded "N million" forms
    (90,421,491 → "90" and "90.4").
    """
    nums: set[str] = set()
    for t in texts:
        nums |= extract_numbers(t, ignore_structural=False)
    derived: set[str] = set()
    for n in nums:
        if n.isdigit() and int(n) >= 1_000_000:
            value = int(n)
            millions = value / 1_000_000
            derived.add(str(int(millions)))
            derived.add(_norm_number(f"{millions:.1f}"))
            derived.add(str(round(millions)))
    return nums | derived


_WORD_RE = re.compile(r"[A-Za-z0-9$%']+(?:[.,][0-9]+)*")


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    ws = words(text)
    if len(ws) < n:
        return {tuple(ws)} if ws else set()
    return {tuple(ws[i : i + n]) for i in range(len(ws) - n + 1)}


def similarity(a: str, b: str, n: int = 3) -> float:
    """Jaccard similarity of word n-grams (0 = different, 1 = identical)."""
    sa, sb = shingles(a, n), shingles(b, n)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ── Text-to-speech normalisation ─────────────────────────────────────────────
_TTS_REPLACEMENTS = [
    (re.compile(r"\bGTA\s*VI\b"), "GTA 6"),
    (re.compile(r"\bGTA\s*V\b"), "GTA 5"),
    (re.compile(r"\bGTA\s*IV\b"), "GTA 4"),
    (re.compile(r"Grand Theft Auto VI\b"), "Grand Theft Auto 6"),
    (re.compile(r"Grand Theft Auto V\b"), "Grand Theft Auto 5"),
    (re.compile(r"Grand Theft Auto IV\b"), "Grand Theft Auto 4"),
    (re.compile(r"\bX\s*\|\s*S\b"), "X and S"),
    (re.compile(r"\bPS5\b"), "PS 5"),
    (re.compile(r"\bRDR\s*2\b"), "Red Dead Redemption 2"),
    (re.compile(r"\bvs\.?\b", re.IGNORECASE), "versus"),
    (re.compile(r"&"), " and "),
    (re.compile(r"\bfps\b", re.IGNORECASE), "F P S"),
]


def tts_normalize(text: str) -> str:
    """Rewrite text so the voice pronounces names and symbols naturally."""
    out = text
    for pattern, repl in _TTS_REPLACEMENTS:
        out = pattern.sub(repl, out)
    return re.sub(r"\s+", " ", out).strip()


_VOWEL_GROUP = re.compile(r"[aeiouy]+", re.IGNORECASE)


def syllable_weight(word: str) -> float:
    """Rough spoken-length weight of a word (used when no phonemes are available)."""
    w = word.strip().lower()
    if not w:
        return 0.5
    digits = sum(ch.isdigit() for ch in w)
    if digits:
        # numbers are spoken as several words ("seventy-nine ninety-nine")
        return 1.2 * digits + 0.5
    groups = len(_VOWEL_GROUP.findall(w))
    if w.endswith("e") and groups > 1:
        groups -= 1
    if w.isupper() and len(w) <= 4:  # acronyms are spelled out (G-T-A)
        return 1.0 * len(w)
    return max(1.0, float(groups)) + 0.15 * len(w) / 4
