from rapidfuzz import fuzz, process

from app.services.name_normalize import extract_country_prefix, normalize_name

# Deliberately model-agnostic (id -> display name) rather than typed to EpgChannel - callers
# look their own objects back up by the returned id.

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
