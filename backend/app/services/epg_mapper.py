import re

from rapidfuzz import fuzz, process

from app.services.name_normalize import extract_country_prefix, normalize_name

# Deliberately model-agnostic (id -> display name) rather than typed to EpgChannel - callers
# look their own objects back up by the returned id.

_DUMMY_EPG_ID_RE = re.compile(r"^dummy[-_]?\d*$", re.IGNORECASE)


def is_dummy_epg_channel_id(epg_channel_id: str) -> bool:
    """True for a provider-generated placeholder guide entry (tvg-id like "dummy-35927") used
    when a channel has no real EPG data - its display name is typically just the channel's own
    raw name echoed back with decorative padding (e.g. "####### TNT SPORT RAW 50 FPS #######"),
    which can spuriously out-score the real candidate on leftover shared noise tokens rather than
    genuine identity, and even when it doesn't, mapping to it is never useful - a dummy entry
    carries no real schedule. Callers building a candidate pool should drop these entirely rather
    than leave the fuzzy scorer to (fail to) deprioritize them."""
    return bool(_DUMMY_EPG_ID_RE.match(epg_channel_id.strip()))

_COUNTRY_MATCH_BONUS = 10.0
"""Added to (or, on a mismatch, subtracted from) a candidate's raw WRatio score (0-100) when
both the channel being matched and the candidate carry a leading country tag (e.g. "US:", "UK:")
- see name_normalize.extract_country_prefix. Same-named channels re-broadcast per region ("ESPN",
"Sky Sports Main Event") are otherwise near-indistinguishable by text alone, so this is meant as
the deciding nudge between close candidates, not an override of the text score itself: a strong
textual match with no detectable country tag on either side is untouched by it, and still beats a
weak textual match that merely happens to share a country tag."""


def search_candidates(channel_name: str, candidates: dict[int, str], limit: int = 10) -> list[tuple[int, float]]:
    if not candidates:
        return []
    query = normalize_name(channel_name)
    query_country = extract_country_prefix(channel_name)
    choices = {cid: normalize_name(name) for cid, name in candidates.items()}
    # A wide-but-bounded shortlist, not every candidate - the country bonus/penalty below is only
    # ever +-_COUNTRY_MATCH_BONUS, so it can't promote a candidate from outside this shortlist
    # (meaningfully lower on raw text score alone) into the final `limit` anyway. Scoring and
    # Python-side-sorting literally every candidate measured ~4x slower against a 20k-channel
    # guide with no change in result for any case tried.
    results = process.extract(query, choices, scorer=fuzz.WRatio, limit=max(limit * 20, 100))
    scored: list[tuple[int, float]] = []
    for _text, score, cid in results:
        candidate_country = extract_country_prefix(candidates[cid])
        if query_country and candidate_country:
            score += _COUNTRY_MATCH_BONUS if candidate_country == query_country else -_COUNTRY_MATCH_BONUS
        scored.append((cid, max(0.0, min(100.0, score))))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [(cid, score / 100.0) for cid, score in scored[:limit]]


def auto_match(channel_name: str, candidates: dict[int, str], sensitivity: float = 0.9) -> tuple[int, float] | None:
    matches = search_candidates(channel_name, candidates, limit=1)
    if not matches:
        return None
    cid, score = matches[0]
    return (cid, score) if score >= sensitivity else None
