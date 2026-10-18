import re

_NOISE_WORDS = r"hd|fhd|uhd|4k|8k|sd|hevc|h265|h264|raw|now|us|usa|uk|ca|vip|backup|feed"
_NOISE_WORD_RE = re.compile(rf"\b(?:{_NOISE_WORDS}|\d{{3,4}}p)\b", re.IGNORECASE)

_LEADING_PREFIX_RE = re.compile(r"^\s*[a-z]{2,12}\s*:\s*", re.IGNORECASE)
"""A short leading tag before a colon (e.g. "UK:", "US:", "NOW:") - providers commonly use these
to group channels by country/network within a single category listing, so it isn't part of what
identifies the channel itself."""

_COUNTRY_PREFIX_RE = re.compile(r"^\s*([a-z]{2,3})\s*:\s*", re.IGNORECASE)
"""Narrower than _LEADING_PREFIX_RE above (2-3 letters only, and capturing) - this one is for
*reading* an actual country/region code (e.g. "US:", "UK:", "AU:") rather than just stripping
noise, so it deliberately excludes longer network-style tags like "SPORTS:" or "BACKUP:" that
_LEADING_PREFIX_RE also strips but that aren't a country signal."""

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def extract_country_prefix(name: str) -> str | None:
    """Pulls the leading country/region code (e.g. "US", "UK", "AU") off a channel name, if it
    has one in that "XX: " form - the same shape normalize_name() strips for text comparison.
    Used by epg_mapper as a same-country signal so fuzzy matching doesn't just pick whichever
    same-named channel scores a point or two higher text-wise, regardless of region. None if the
    name doesn't start with one (most EPG guide display names won't - that's fine, it's only ever
    used as a bonus/penalty, never a hard requirement)."""
    m = _COUNTRY_PREFIX_RE.match(name)
    return m.group(1).upper() if m else None


def normalize_name(name: str) -> str:
    """Strips a leading "REGION:" style prefix and quality/source tags (HD, FHD, RAW, 4K, VIP,
    ...) wherever they appear, then collapses punctuation/whitespace/case.

    Shared by EPG fuzzy matching (epg_mapper) and duplicate-channel grouping (duplicate_scanner)
    so a channel like "UK: BBC One HD" is recognized the same way - as "bbc one" - in both
    places, regardless of how a provider decorated its name.
    """
    s = _LEADING_PREFIX_RE.sub(" ", name)
    s = _NOISE_WORD_RE.sub(" ", s)
    s = _PUNCTUATION_RE.sub(" ", s)
    return _WHITESPACE_RE.sub(" ", s).strip().lower()
