import re
import unicodedata

_NOISE_WORDS = r"hd|fhd|uhd|4k|8k|sd|hevc|h265|h264|raw|now|us|usa|uk|ca|vip|backup|feed|fps"
_NOISE_WORD_RE = re.compile(rf"\b(?:{_NOISE_WORDS}|\d{{3,4}}p|\d{{2,3}}\s*fps)\b", re.IGNORECASE)
"""Also catches a resolution tag (1080p, 720p) and a frame-rate tag (50 FPS, 60FPS) - left
unstripped, a frame-rate tag that happens to match between the channel being matched and an
unrelated-but-identically-decorated EPG candidate ("TNT Sport RAW 50 FPS") was a real false
signal: it could out-score the actual, undecorated match ("TNT Sports 4") on shared "50 fps"
tokens alone, rather than on channel identity. Bare "fps" is also in _NOISE_WORDS itself (not
just the digit-prefixed pattern) since _SUPERSCRIPT_RE below can strip a superscript-styled "50"
before this runs, orphaning a plain "FPS" with no digits in front of it to match against."""

_SUPERSCRIPT_RE = re.compile(
    "[²³¹⁰-₟ᴬ-ᵪᶛ-ᶿʰ-ʸˠ-ˤꟸꟹ]+"
)
"""Superscript/subscript digits (⁵⁰, ², ³, ¹) and the Latin "small capital" modifier letters a
superscript-text generator uses for letters (ᴿᴬᵂ for "RAW") - covers the Unicode "Superscripts
and Subscripts" block plus the two "Spacing Modifier Letters"/"Phonetic Extensions Supplement"
blocks those generators draw from. Stripped outright, not folded back to plain text: a provider
uses superscript specifically to visually de-emphasize a tag as secondary annotation (exactly
like a parenthetical), so whatever word it spells is decoration regardless of whether that word
happens to already be on the plain-ASCII noise list above. Also the modifier letters from
"Spacing Modifier Letters" (ʰ-ʸ, ˠ-ˤ: "ʰᵈ" for "HD") and the two Latin Extended-D superscript
capitals (ꟸ, ꟹ) - the same set DPTV-Player treats as a provider's tag marks."""

_LEADING_PREFIX_RE = re.compile(
    r"^\s*(?:[a-z]{2,12}\s*:|\|?\s*[a-z]{2,12}\s*\||[\[(]\s*[a-z]{2,12}\s*[\])])\s*", re.IGNORECASE
)
"""A short leading tag (e.g. "UK:", "US:", "NOW:", and the bar and bracket forms "DE|", "DE |",
"|UK|", "[UK]", "(UK)") - providers commonly use these to group channels by country/network
within a single category listing, so it isn't part of what identifies the channel itself."""

_COUNTRY_PREFIX_RE = re.compile(
    r"^\s*(?:([a-z]{2,3})\s*:|\|?\s*([a-z]{2,3})\s*\||[\[(]\s*([a-z]{2,3})\s*[\])])\s*", re.IGNORECASE
)
"""Narrower than _LEADING_PREFIX_RE above (2-3 letters only, and capturing) - this one is for
*reading* an actual country/region code (e.g. "US:", "UK:", "AU:") rather than just stripping
noise, so it deliberately excludes longer network-style tags like "SPORTS:" or "BACKUP:" that
_LEADING_PREFIX_RE also strips but that aren't a country signal."""

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _canonicalize(name: str) -> str:
    """Strips superscript decoration outright (see _SUPERSCRIPT_RE), then folds any other
    Unicode "compatibility" variant - fullwidth forms and the like - back to plain text via NFKC.
    Some providers stylize quality/format tags this way (e.g. "ᴿᴬᵂ ⁵⁰ FPS"), and left alone
    these are invisible to every ASCII-only regex below: not stripped as noise, not even visible
    as the letters/digits they resemble, just silently sitting in the normalized text as extra
    unmatched tokens that drag a channel's real, otherwise-exact candidate's score down (and do
    the same, roughly evenly, to every other candidate - the "nothing scores above ~90%, several
    unrelated candidates tie" symptom this was causing)."""
    s = _SUPERSCRIPT_RE.sub("", name)
    return unicodedata.normalize("NFKC", s)


def extract_country_prefix(name: str) -> str | None:
    """Pulls the leading country/region code (e.g. "US", "UK", "AU") off a channel name, if it
    has one in that "XX: " form (or "XX|", "|XX|", "[XX]", "(XX)") - the same shape normalize_name() strips for text comparison.
    Used by epg_mapper as a same-country signal so fuzzy matching doesn't just pick whichever
    same-named channel scores a point or two higher text-wise, regardless of region. None if the
    name doesn't start with one (most EPG guide display names won't - that's fine, it's only ever
    used as a bonus/penalty, never a hard requirement)."""
    m = _COUNTRY_PREFIX_RE.match(_canonicalize(name))
    if not m:
        return None
    return next(g for g in m.groups() if g).upper()


def normalize_name(name: str) -> str:
    """Strips a leading "REGION:" style prefix and quality/source tags (HD, FHD, RAW, 4K, VIP,
    ...) wherever they appear, then collapses punctuation/whitespace/case.

    Shared by EPG fuzzy matching (epg_mapper) and duplicate-channel grouping (duplicate_scanner)
    so a channel like "UK: BBC One HD" is recognized the same way - as "bbc one" - in both
    places, regardless of how a provider decorated its name.
    """
    s = _canonicalize(name)
    s = _LEADING_PREFIX_RE.sub(" ", s)
    s = _NOISE_WORD_RE.sub(" ", s)
    s = _PUNCTUATION_RE.sub(" ", s)
    return _WHITESPACE_RE.sub(" ", s).strip().lower()
