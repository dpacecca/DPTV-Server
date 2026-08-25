import re

_QUALITY_TOKEN = r"(?:U?HD|FHD|SD|\d{3,4}p|4K|8K)"
_BRACKETED_TAG = re.compile(rf"[\(\[]\s*{_QUALITY_TOKEN}\s*[\)\]]", re.IGNORECASE)
_BARE_TAG = re.compile(rf"(?<!\w){_QUALITY_TOKEN}(?!\w)", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

TRAILING_QUALITY_TAG = re.compile(rf"\s*[\(\[]\s*{_QUALITY_TOKEN}\s*[\)\]]\s*$", re.IGNORECASE)
"""Matches a quality tag this feature itself would have appended (e.g. " [1080p]"), so
re-tagging after a re-scan replaces the old tag instead of stacking a new one onto it."""


def normalize_channel_name(name: str) -> str:
    """Strips quality/resolution tags (HD, FHD, 1080p, 4K, ...) so e.g. "CNN HD" and
    "CNN FHD" both normalize to "cnn" and are recognized as the same channel at different
    quality levels. Case/punctuation-insensitive.

    Deliberately an exact match after normalization, not a fuzzy one - grouping two channels
    that aren't actually the same one, and later deleting one of them, is a much worse failure
    mode than missing a duplicate whose names differ in some other way.
    """
    s = _BRACKETED_TAG.sub(" ", name)
    s = _BARE_TAG.sub(" ", s)
    s = _PUNCTUATION.sub(" ", s)
    s = _WHITESPACE.sub(" ", s).strip().lower()
    return s


_RESOLUTION_BUCKETS = [
    (2160, "2160p"),
    (1440, "1440p"),
    (1080, "1080p"),
    (720, "720p"),
    (576, "576p"),
    (480, "480p"),
    (360, "360p"),
    (240, "240p"),
]


def resolution_label(height: int | None) -> str | None:
    """Buckets a detected pixel height to the nearest standard broadcast resolution label.
    Real streams rarely report an exact 1080/720 (e.g. 1072, 1088), but should still be
    labeled the way that standard is normally called."""
    if not height:
        return None
    for threshold, label in _RESOLUTION_BUCKETS:
        if height >= threshold - 40:
            return label
    return "SD"


def _rank_key(probe: dict) -> tuple:
    """Sort key for ranking duplicate candidates best-first: successful probes win over failed
    ones, then higher resolution/framerate/bitrate wins."""
    ok = probe.get("status") == "ok"
    return (
        1 if ok else 0,
        probe.get("height") or 0,
        probe.get("fps") or 0,
        probe.get("bitrate_kbps") or 0,
    )


def group_duplicates(channels: list[dict]) -> list[dict]:
    """channels: [{"channel_id", "name", "status", "height", "fps", "bitrate_kbps", ...}, ...],
    already scoped to a single category. Groups by normalized name; only groups with more than
    one member are real duplicate candidates. Each group's channel_ids are ordered best first.

    best_channel_id is None when no member of the group probed successfully - there's no
    reliable pick in that case, so the caller shouldn't pre-select anything for removal.
    """
    groups: dict[str, list[dict]] = {}
    for ch in channels:
        key = normalize_channel_name(ch["name"])
        groups.setdefault(key, []).append(ch)

    result = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=_rank_key, reverse=True)
        best = members_sorted[0]
        result.append(
            {
                "key": key,
                "channel_ids": [m["channel_id"] for m in members_sorted],
                "best_channel_id": best["channel_id"] if best.get("status") == "ok" else None,
            }
        )
    return result
