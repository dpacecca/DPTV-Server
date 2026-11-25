"""(1.0.24) Movie categories for players: New releases, Trending this week, one per TMDB genre and one
per decade, built from the provider's films.

Each film's release date comes from the provider when its list carries one, else from TMDB, else from
the year in its title. Genres, rating and popularity come from TMDB: a film is looked up by the
provider's TMDB id when it has one, else searched by its cleaned title and year. Films in a playlist
category whose name contains one of the excluded words (default "For Adults") are left out.

Players get the categories from player_api.php?action=dptv_smart_categories.
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.base import ChannelType
from app.models.movie_info import MovieInfo, TmdbTrending
from app.models.playlist import Playlist
from app.models.source import SourceChannel

log = logging.getLogger("dptv.movie_info")

TMDB = "https://api.themoviedb.org/3"
_BATCH = 200
_CONCURRENCY = 6
_RETRY_NOT_FOUND = timedelta(days=14)
_PROGRESS_EVERY = 1000
_lock = asyncio.Lock()
_genre_names: dict[int, str] = {}


# --- what the provider and the title say -------------------------------------------------------

def provider_release(value) -> str | None:
    """A release date as the provider writes it ("2023-05-12", "2023-05-12 00:00:00", "12/05/2023",
    "2023"): "YYYY-MM-DD", or "YYYY" for a year alone; None for anything else."""
    text = str(value or "").strip()
    if not text:
        return None
    m = re.match(r"^((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return str(y)
    m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.]((?:19|20)\d{2})$", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return m.group(3)
    m = re.fullmatch(r"(?:19|20)\d{2}", text)
    return m.group(0) if m else None


_YEAR = re.compile(r"[\(\[]((?:19|20)\d{2})[\)\]]|\s-\s((?:19|20)\d{2})\s*$")
_PREFIX = re.compile(r"^(?:\|[^|]{1,12}\|\s*|\[[^\]]{1,12}\]\s*|[A-Z0-9][A-Z0-9+\-]{1,10}\s+-\s+|[A-Z]{2,3}\s*[:|]\s*)")
_TAGS = re.compile(r"\b(?:4K|8K|UHD|FHD|HD|SD|HDR|HEVC|H\.?265|MULTI|SUB|DUB|VOSE|CAM|TS)\b", re.I)


def title_year(name: str) -> int | None:
    m = _YEAR.search(name or "")
    return int(m.group(1) or m.group(2)) if m else None


def clean_title(name: str) -> str:
    """"4K-AMZ - Elf Me (2023)" → "Elf Me"; "|EN| Madden (2026) (US)" → "Madden"."""
    t = (name or "").strip()
    for _ in range(3):
        stripped = _PREFIX.sub("", t)
        if stripped == t:
            break
        t = stripped
    t = re.split(r"\s*[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]", t)[0]
    t = re.sub(r"\s-\s(?:19|20)\d{2}\s*$", "", t)
    t = re.sub(r"\s*[\(\[][^\)\]]{1,12}[\)\]]\s*$", "", t)
    t = _TAGS.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -|:")


def year_of(release: str | None) -> int | None:
    return int(release[:4]) if release and release[:4].isdigit() else None


# --- TMDB --------------------------------------------------------------------------------------

def _tmdb_auth(key: str) -> tuple[dict, dict]:
    """A v4 read access token (a JWT) goes in the header; a v3 key as api_key."""
    key = key.strip()
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}", "Accept": "application/json"}, {}
    return {"Accept": "application/json"}, {"api_key": key}


async def _tmdb_get(client: httpx.AsyncClient, key: str, path: str, **params) -> dict | None:
    headers, auth = _tmdb_auth(key)
    for attempt in range(3):
        r = await client.get(f"{TMDB}{path}", params={**auth, **params}, headers=headers)
        if r.status_code == 429:
            await asyncio.sleep(float(r.headers.get("Retry-After") or 2) + attempt)
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    return None


async def _genres(client: httpx.AsyncClient, key: str) -> dict[int, str]:
    if not _genre_names:
        data = await _tmdb_get(client, key, "/genre/movie/list", language="en-US") or {}
        _genre_names.update({g["id"]: g["name"] for g in data.get("genres", []) if "id" in g and "name" in g})
    return _genre_names


async def _lookup(client: httpx.AsyncClient, key: str, sc: SourceChannel) -> dict | None:
    """TMDB's film for this channel: by the provider's TMDB id, else the first search result."""
    if sc.provider_tmdb_id:
        found = await _tmdb_get(client, key, f"/movie/{sc.provider_tmdb_id}", language="en-US")
        if found:
            return {
                "id": found.get("id"), "release_date": found.get("release_date"),
                "vote_average": found.get("vote_average"), "vote_count": found.get("vote_count"),
                "popularity": found.get("popularity"), "genres": [g.get("name") for g in found.get("genres", [])],
            }
    title = clean_title(sc.name)
    if not title:
        return None
    year = year_of(sc.provider_release) or title_year(sc.name)
    params = {"query": title, "include_adult": "false", "language": "en-US"}
    data = await _tmdb_get(client, key, "/search/movie", **params, **({"year": year} if year else {}))
    results = (data or {}).get("results") or []
    if not results and year:
        data = await _tmdb_get(client, key, "/search/movie", **params)
        results = [r for r in (data or {}).get("results") or [] if year_of(r.get("release_date")) in (year - 1, year, year + 1)]
    if not results:
        return None
    best = results[0]
    names = await _genres(client, key)
    return {
        "id": best.get("id"), "release_date": best.get("release_date"),
        "vote_average": best.get("vote_average"), "vote_count": best.get("vote_count"),
        "popularity": best.get("popularity"), "genres": [names[g] for g in best.get("genre_ids", []) if g in names],
    }


def _info_from(sc: SourceChannel, found: dict | None, checked: bool, now: datetime) -> dict:
    tmdb_release = provider_release((found or {}).get("release_date"))
    release = sc.provider_release if sc.provider_release and len(sc.provider_release) == 10 else None
    release = release or tmdb_release or sc.provider_release or (str(title_year(sc.name)) if title_year(sc.name) else None)
    votes = (found or {}).get("vote_count") or 0
    tmdb_rating = (found or {}).get("vote_average") if votes >= 5 else None
    return {
        "source_channel_id": sc.id,
        "tmdb_id": (found or {}).get("id"),
        "release_date": release,
        "rating": tmdb_rating or sc.provider_rating,
        "popularity": (found or {}).get("popularity"),
        "genres": json.dumps([g for g in (found or {}).get("genres", []) if g]) if found else None,
        "tmdb_checked": checked,
        "looked_up_at": now,
    }


def _pending_filter(q, with_key: bool, after_id: int = 0):
    retry_before = datetime.now(timezone.utc) - _RETRY_NOT_FOUND
    q = (
        q.outerjoin(MovieInfo, MovieInfo.source_channel_id == SourceChannel.id)
        .where(SourceChannel.stream_type == ChannelType.VOD, SourceChannel.removed_at.is_(None), SourceChannel.id > after_id)
    )
    if with_key:
        return q.where(or_(
            MovieInfo.source_channel_id.is_(None),
            MovieInfo.tmdb_checked.is_(False),
            and_(MovieInfo.tmdb_id.is_(None), MovieInfo.looked_up_at < retry_before),
        ))
    return q.where(MovieInfo.source_channel_id.is_(None))


async def _pending(db: AsyncSession, with_key: bool, limit: int, after_id: int = 0) -> list[SourceChannel]:
    q = _pending_filter(select(SourceChannel), with_key, after_id)
    return list((await db.execute(q.order_by(SourceChannel.id).limit(limit))).scalars().all())


async def _pending_count(db: AsyncSession, with_key: bool) -> int:
    """(1.0.25) How many films a run has ahead of it, for its progress lines."""
    q = _pending_filter(select(func.count(SourceChannel.id)).select_from(SourceChannel), with_key)
    return int((await db.execute(q)).scalar() or 0)


async def _save(db: AsyncSession, rows: list[dict]) -> None:
    for row in rows:
        existing = await db.get(MovieInfo, row["source_channel_id"])
        if existing is None:
            db.add(MovieInfo(**row))
        else:
            for k, v in row.items():
                setattr(existing, k, v)
    await db.commit()


async def refresh_trending(db: AsyncSession, client: httpx.AsyncClient, key: str) -> int:
    ids: list[int] = []
    for page in (1, 2, 3):
        data = await _tmdb_get(client, key, "/trending/movie/week", page=page, language="en-US") or {}
        ids.extend(r["id"] for r in data.get("results", []) if r.get("id") and r["id"] not in ids)
    if not ids:
        return 0
    now = datetime.now(timezone.utc)
    await db.execute(delete(TmdbTrending))
    db.add_all(TmdbTrending(tmdb_id=i, rank=n, fetched_at=now) for n, i in enumerate(ids))
    await db.commit()
    return len(ids)


async def run_movie_info(db: AsyncSession, max_batches: int = 1000) -> dict:
    """Fill in every film not looked up yet (and, with a TMDB key, retry ones TMDB didn't find after
    two weeks), and refresh this week's trending films. Safe to call often: one run at a time."""
    if _lock.locked():
        return {"skipped": "already running"}
    async with _lock:
        key = get_settings().tmdb_api_key.strip()
        done = found = trending = 0
        async with httpx.AsyncClient(timeout=20) as client:
            if key:
                try:
                    trending = await refresh_trending(db, client, key)
                except Exception as exc:  # noqa: BLE001 - trending only refines the categories
                    log.warning("TMDB trending failed: %s", type(exc).__name__)
            sem = asyncio.Semaphore(_CONCURRENCY)

            async def one(sc: SourceChannel) -> dict:
                now = datetime.now(timezone.utc)
                if not key:
                    return _info_from(sc, None, False, now)
                async with sem:
                    try:
                        return _info_from(sc, await _lookup(client, key, sc), True, now)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 401:
                            raise
                        return _info_from(sc, None, False, now)
                    except Exception:  # noqa: BLE001 - one film never stops the run
                        return _info_from(sc, None, False, now)

            total = await _pending_count(db, bool(key))
            if total:
                # (1.0.25) Progress in the journal: a start line, then one every 1000 films.
                log.info("movie info: starting, %d films to look up (%s)", total,
                         "with TMDB" if key else "no TMDB key: provider details and title years only")
            next_report = _PROGRESS_EVERY
            last_id = 0  # each film once per run, even one TMDB failed on
            for _ in range(max_batches):
                batch = await _pending(db, bool(key), _BATCH, last_id)
                if not batch:
                    break
                last_id = batch[-1].id
                try:
                    rows = await asyncio.gather(*(one(sc) for sc in batch))
                except httpx.HTTPStatusError:
                    log.warning("TMDB refused the API key; films keep the provider's details only")
                    key = ""
                    rows = [_info_from(sc, None, False, datetime.now(timezone.utc)) for sc in batch]
                await _save(db, rows)
                done += len(rows)
                found += sum(1 for r in rows if r["tmdb_id"])
                if done >= next_report and done < total:
                    log.info("movie info: %d of %d looked up, %d found on TMDB", done, total, found)
                    next_report = (done // _PROGRESS_EVERY + 1) * _PROGRESS_EVERY
                if all(not r["tmdb_checked"] for r in rows) and key:
                    break  # TMDB answering nothing at all: try again on the next run
        log.info("movie info: %d films looked up, %d found on TMDB, %d trending", done, found, trending)
        return {"looked_up": done, "found": found, "trending": trending}


# --- the categories ----------------------------------------------------------------------------

def _excluded(category_name: str, words: list[str]) -> bool:
    name = (category_name or "").casefold()
    return any(w and w in name for w in words)


async def build_categories(db: AsyncSession, playlists: list[Playlist]) -> dict:
    """The movie categories for these playlists (a login's enabled ones), as players get them:
    {"categories": [{"id", "name", "kind", "streams": [stream ids]}], "films": {stream id: [release, rating]}}."""
    settings = get_settings()
    words = [w.strip().casefold() for w in settings.smart_categories_exclude.split(",") if w.strip()]
    films: dict[int, SourceChannel] = {}
    seen_sources: set[int] = set()
    for pl in playlists:
        for cat in pl.categories:
            if cat.channel_type != ChannelType.VOD or _excluded(cat.name, words):
                continue
            for pc in cat.channels:
                sc = pc.source_channel
                if not pc.enabled or sc is None or sc.id in seen_sources:
                    continue
                seen_sources.add(sc.id)
                films[pc.id] = sc
    if not films:
        return {"categories": [], "films": {}}

    infos: dict[int, MovieInfo] = {}
    ids = [sc.id for sc in films.values()]
    for i in range(0, len(ids), 5000):
        rows = (await db.execute(select(MovieInfo).where(MovieInfo.source_channel_id.in_(ids[i:i + 5000])))).scalars().all()
        infos.update({r.source_channel_id: r for r in rows})
    trending_rank = {t.tmdb_id: t.rank for t in (await db.execute(select(TmdbTrending))).scalars().all()}

    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=max(settings.smart_new_release_days, 1))).isoformat()
    out_films: dict[str, list] = {}
    new, trending = [], []
    genres: dict[str, list] = defaultdict(list)
    decades: dict[int, list] = defaultdict(list)
    for stream_id, sc in films.items():
        info = infos.get(sc.id)
        # The provider's full date always wins, even over one looked up before the sync that brought it.
        full = sc.provider_release if sc.provider_release and len(sc.provider_release) == 10 else None
        release = full or (info.release_date if info else None) or sc.provider_release or (
            str(title_year(sc.name)) if title_year(sc.name) else None)
        rating = (info.rating if info else None) or sc.provider_rating
        popularity = (info.popularity if info else None) or 0.0
        out_films[str(stream_id)] = [release, round(rating, 1) if rating else None]
        if release and len(release) == 10 and since <= release <= today.isoformat():
            new.append((release, stream_id))
        if info and info.tmdb_id in trending_rank:
            trending.append((trending_rank[info.tmdb_id], stream_id))
        for g in json.loads(info.genres) if info and info.genres else []:
            genres[g].append((-popularity, stream_id))
        year = year_of(release)
        if year and 1900 <= year <= today.year + 1:
            decades[year // 10 * 10].append((release, stream_id))

    categories = []
    if trending:
        categories.append({"id": "trending", "name": "Trending this week", "kind": "trending",
                           "streams": [s for _, s in sorted(trending)]})
    if new:
        categories.append({"id": "new", "name": "New releases", "kind": "new",
                           "streams": [s for _, s in sorted(new, reverse=True)]})
    for name in sorted(genres):
        categories.append({"id": "genre:" + name.casefold().replace(" ", "-"), "name": name, "kind": "genre",
                           "streams": [s for _, s in sorted(genres[name])]})
    for decade in sorted(decades, reverse=True):
        categories.append({"id": f"decade:{decade}", "name": f"{decade}s", "kind": "decade",
                           "streams": [s for _, s in sorted(decades[decade], reverse=True)]})
    return {"categories": categories, "films": out_films}
