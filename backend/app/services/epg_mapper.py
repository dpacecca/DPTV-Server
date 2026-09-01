from rapidfuzz import fuzz, process

from app.services.name_normalize import normalize_name

# Deliberately model-agnostic (id -> display name) rather than typed to EpgChannel - the same
# fuzzy-matching logic is used to match against both real EpgChannel rows and the persistent
# IptvOrgChannel catalog. Callers look their own objects back up by the returned id.


def search_candidates(channel_name: str, candidates: dict[int, str], limit: int = 10) -> list[tuple[int, float]]:
    query = normalize_name(channel_name)
    if not candidates:
        return []
    choices = {cid: normalize_name(name) for cid, name in candidates.items()}
    results = process.extract(query, choices, scorer=fuzz.WRatio, limit=limit)
    return [(cid, score / 100.0) for _text, score, cid in results]


def auto_match(channel_name: str, candidates: dict[int, str], sensitivity: float = 0.9) -> tuple[int, float] | None:
    matches = search_candidates(channel_name, candidates, limit=1)
    if not matches:
        return None
    cid, score = matches[0]
    return (cid, score) if score >= sensitivity else None
