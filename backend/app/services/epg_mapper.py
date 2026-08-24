import re

from rapidfuzz import fuzz, process

from app.models.epg import EpgChannel

_NOISE_RE = re.compile(
    r"\b(hd|fhd|uhd|4k|sd|hevc|h265|h264|us|usa|uk|ca|vip|backup|feed)\b|[^\w\s]", re.IGNORECASE
)


def normalize_name(name: str) -> str:
    name = _NOISE_RE.sub(" ", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def search_candidates(
    channel_name: str, candidates: list[EpgChannel], limit: int = 10
) -> list[tuple[EpgChannel, float]]:
    query = normalize_name(channel_name)
    if not candidates:
        return []
    choices = {c.id: normalize_name(c.display_name) for c in candidates}
    results = process.extract(query, choices, scorer=fuzz.WRatio, limit=limit)
    by_id = {c.id: c for c in candidates}
    return [(by_id[cid], score / 100.0) for _text, score, cid in results]


def auto_match(channel_name: str, candidates: list[EpgChannel], sensitivity: float = 0.9) -> EpgChannel | None:
    matches = search_candidates(channel_name, candidates, limit=1)
    if not matches:
        return None
    best, score = matches[0]
    return best if score >= sensitivity else None
