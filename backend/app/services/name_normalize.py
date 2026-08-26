import re

_NOISE_WORDS = r"hd|fhd|uhd|4k|8k|sd|hevc|h265|h264|raw|now|us|usa|uk|ca|vip|backup|feed"
_NOISE_WORD_RE = re.compile(rf"\b(?:{_NOISE_WORDS}|\d{{3,4}}p)\b", re.IGNORECASE)

_LEADING_PREFIX_RE = re.compile(r"^\s*[a-z]{2,12}\s*:\s*", re.IGNORECASE)
"""A short leading tag before a colon (e.g. "UK:", "US:", "NOW:") - providers commonly use these
to group channels by country/network within a single category listing, so it isn't part of what
identifies the channel itself."""

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


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
