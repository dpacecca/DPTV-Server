"""Cheap "has anything this XC user can see changed?" revisions.

Backs the `player_api.php?action=dptv_sync_status` action and the ETag/Last-Modified validators
on get.php, xmltv.php and the player_api.php list actions (see api/routes/xc_server.py), so a
client such as DPTV-Player can poll every few minutes and only re-download a playlist or guide
when it actually changed.

Each revision is a short hash over a handful of aggregate queries - max(updated_at), count(*) and
sum(id) per table and channel type, scoped to the user's enabled playlists (the same scope as
xc_server._enabled_playlists) - never a full playlist load:

* max(updated_at) catches edits: every model involved uses TimestampMixin, whose onupdate=now()
  fires for ORM flushes and Core UPDATE statements alike (only a statement that sets updated_at
  itself skips it - see the bookkeeping write in sync_engine.sync_source, which does that on
  purpose so a sync that changes nothing doesn't look like a change).
* count(*) and sum(id) catch deletions and swaps, which leave no timestamp behind.
* count(source_channel_id) / count(epg_channel_id) catch the database-level ON DELETE SET NULL
  that happens when a Source or EPG source/channel is deleted - that bypasses onupdate.
"""

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.base import ChannelType
from app.models.epg import EpgChannel, EpgSource
from app.models.playlist import (
    DummyEpgRule,
    Playlist,
    PlaylistCategory,
    PlaylistChannel,
)
from app.models.source import Source, SourceCategory, SourceChannel
from app.models.xc_user import XcUser, XcUserPlaylist

KINDS = ("live", "vod", "series", "epg")
_TYPE_KIND = {ChannelType.LIVE: "live", ChannelType.VOD: "vod", ChannelType.SERIES: "series"}


@dataclass
class Rev:
    rev: str
    updated_at: datetime | None
    """Newest timestamp among the rows behind this revision. Informational: a deletion changes
    `rev` without moving this, so clients should compare `rev`, not this."""


@dataclass
class SyncStatus:
    revs: dict[str, Rev]
    last_source_sync_at: datetime | None
    last_epg_refresh_at: datetime | None

    def combined(self, kinds: tuple[str, ...]) -> Rev:
        """One revision over several kinds (get.php covers live+vod+series)."""
        parts = [self.revs[k] for k in kinds]
        return Rev(
            rev=_hash([p.rev for p in parts]),
            updated_at=_max(*(p.updated_at for p in parts)),
        )


def _hash(parts: list) -> str:
    return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:16]


def _norm(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    # SQLite (tests) hands back naive datetimes; everything is stored as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _max(*values: datetime | None) -> datetime | None:
    present = [_norm(v) for v in values if v is not None]
    return max(present) if present else None


def _iso(dt: datetime | None) -> str | None:
    dt = _norm(dt)
    return dt.isoformat() if dt else None


def unix(dt: datetime | None) -> int | None:
    dt = _norm(dt)
    return int(dt.timestamp()) if dt else None


async def compute_sync_status(db: AsyncSession, user: XcUser) -> SyncStatus:
    settings = get_settings()

    # 1. Which playlists this user's player sees (same filter as xc_server._enabled_playlists).
    pl_rows = (
        await db.execute(
            select(Playlist.id, Playlist.updated_at)
            .join(XcUserPlaylist, XcUserPlaylist.playlist_id == Playlist.id)
            .where(
                XcUserPlaylist.xc_user_id == user.id,
                XcUserPlaylist.enabled.is_(True),
                Playlist.enabled.is_(True),
                Playlist.xc_enabled.is_(True),
            )
            .order_by(Playlist.id)
        )
    ).all()
    playlist_ids = [r[0] for r in pl_rows]
    playlists_max = _max(*(r[1] for r in pl_rows))
    # Stream URLs in get.php embed the user's credentials and the public base URL, so an edit to
    # either has to read as a change too.
    common = [
        [(r[0], _iso(r[1])) for r in pl_rows],
        _iso(user.updated_at),
        settings.public_base_url,
    ]

    per_type: dict[str, list] = {k: [] for k in ("live", "vod", "series")}
    per_type_max: dict[str, datetime | None] = {k: None for k in ("live", "vod", "series")}
    last_source_sync_at: datetime | None = None
    rules_part: list = []
    rules_max: datetime | None = None
    epg_part: list = []
    last_epg_refresh_at: datetime | None = None
    epg_sources_max: datetime | None = None

    if playlist_ids:
        # 2. Categories per type.
        cat_rows = (
            await db.execute(
                select(
                    PlaylistCategory.channel_type,
                    func.count(PlaylistCategory.id),
                    func.sum(PlaylistCategory.id),
                    func.max(PlaylistCategory.updated_at),
                )
                .where(PlaylistCategory.playlist_id.in_(playlist_ids))
                .group_by(PlaylistCategory.channel_type)
            )
        ).all()
        for ctype, count, id_sum, max_upd in cat_rows:
            kind = _TYPE_KIND[ChannelType(ctype)]
            per_type[kind].append(("cat", count, id_sum, _iso(max_upd)))
            per_type_max[kind] = _max(per_type_max[kind], max_upd)

        # 3. Channels per type (edits, reorders, enable/disable, manual URL, EPG mapping...).
        chan_rows = (
            await db.execute(
                select(
                    PlaylistCategory.channel_type,
                    func.count(PlaylistChannel.id),
                    func.sum(PlaylistChannel.id),
                    func.max(PlaylistChannel.updated_at),
                    func.count(PlaylistChannel.source_channel_id),
                    func.count(PlaylistChannel.epg_channel_id),
                    func.count(PlaylistChannel.dummy_epg_rule_id),
                )
                .join(PlaylistCategory, PlaylistCategory.id == PlaylistChannel.playlist_category_id)
                .where(PlaylistCategory.playlist_id.in_(playlist_ids))
                .group_by(PlaylistCategory.channel_type)
            )
        ).all()
        for ctype, count, id_sum, max_upd, n_src, n_epg, n_rule in chan_rows:
            kind = _TYPE_KIND[ChannelType(ctype)]
            per_type[kind].append(("chan", count, id_sum, _iso(max_upd), n_src, n_epg, n_rule))
            per_type_max[kind] = _max(per_type_max[kind], max_upd)

        # 4. Provider data behind those channels (name/logo/stream URL, updated by source sync).
        src_rows = (
            await db.execute(
                select(
                    PlaylistCategory.channel_type,
                    func.max(SourceChannel.updated_at),
                    func.max(Source.last_sync_at),
                )
                .select_from(PlaylistChannel)
                .join(PlaylistCategory, PlaylistCategory.id == PlaylistChannel.playlist_category_id)
                .join(SourceChannel, SourceChannel.id == PlaylistChannel.source_channel_id)
                .join(SourceCategory, SourceCategory.id == SourceChannel.source_category_id)
                .join(Source, Source.id == SourceCategory.source_id)
                .where(PlaylistCategory.playlist_id.in_(playlist_ids))
                .group_by(PlaylistCategory.channel_type)
            )
        ).all()
        for ctype, max_upd, max_sync in src_rows:
            kind = _TYPE_KIND[ChannelType(ctype)]
            per_type[kind].append(("src", _iso(max_upd)))
            per_type_max[kind] = _max(per_type_max[kind], max_upd)
            last_source_sync_at = _max(last_source_sync_at, max_sync)

        # 5. Dummy EPG rules of those playlists.
        rule_row = (
            await db.execute(
                select(func.count(DummyEpgRule.id), func.sum(DummyEpgRule.id), func.max(DummyEpgRule.updated_at)).where(
                    DummyEpgRule.playlist_id.in_(playlist_ids)
                )
            )
        ).one()
        rules_part = [rule_row[0], rule_row[1], _iso(rule_row[2])]
        rules_max = _norm(rule_row[2])

        # 6. EPG sources whose channels are mapped in those playlists - their refresh time stands
        # in for the programme data itself (a refresh replaces it wholesale).
        epg_row = (
            await db.execute(
                select(
                    func.count(func.distinct(EpgSource.id)),
                    func.max(EpgSource.last_refreshed_at),
                    func.max(EpgSource.updated_at),
                )
                .select_from(PlaylistChannel)
                .join(PlaylistCategory, PlaylistCategory.id == PlaylistChannel.playlist_category_id)
                .join(EpgChannel, EpgChannel.id == PlaylistChannel.epg_channel_id)
                .join(EpgSource, EpgSource.id == EpgChannel.epg_source_id)
                .where(PlaylistCategory.playlist_id.in_(playlist_ids))
            )
        ).one()
        epg_part = [epg_row[0], _iso(epg_row[1]), _iso(epg_row[2])]
        last_epg_refresh_at = _norm(epg_row[1])
        epg_sources_max = _max(epg_row[1], epg_row[2])

    revs: dict[str, Rev] = {}
    for kind in ("live", "vod", "series"):
        revs[kind] = Rev(
            rev=_hash([kind, common, sorted(per_type[kind], key=repr)]),
            updated_at=_max(per_type_max[kind], playlists_max, user.updated_at) if playlist_ids else None,
        )

    # xmltv.php lists every enabled channel of every type (names, icons, dummy programmes), so the
    # guide revision covers all three types plus the guide-only inputs. It also rolls over once
    # per UTC day: the feed is a sliding window from "now", so even with no data change a client
    # needs a fresh copy at least daily.
    utc_day = datetime.now(timezone.utc).date().isoformat()
    revs["epg"] = Rev(
        rev=_hash(
            [
                "epg",
                common,
                [sorted(per_type[k], key=repr) for k in ("live", "vod", "series")],
                rules_part,
                epg_part,
                utc_day,
            ]
        ),
        updated_at=_max(*(per_type_max.values()), rules_max, epg_sources_max, playlists_max, user.updated_at)
        if playlist_ids
        else None,
    )

    return SyncStatus(revs=revs, last_source_sync_at=last_source_sync_at, last_epg_refresh_at=last_epg_refresh_at)


# --- Last-Modified bookkeeping ------------------------------------------------------------------
#
# A deletion changes a revision without leaving any newer timestamp behind, so max(updated_at)
# alone can't back an If-Modified-Since check - a client that only sends If-Modified-Since would
# get a wrong 304 after a channel is deleted. Instead Last-Modified is the time this process
# first saw the current revision (never earlier than the data's own newest timestamp, nor than
# process start, so a restart can only cause one extra refetch, never a wrong 304). ETag /
# If-None-Match is exact and is what clients should prefer.

_PROCESS_START = datetime.now(timezone.utc).replace(microsecond=0)
_observed: dict[tuple[int, str], tuple[str, datetime]] = {}
_MAX_OBSERVED = 10_000


def last_modified_for(user_id: int, key: str, rev: Rev) -> datetime:
    """Last-Modified (whole seconds, as HTTP carries it) for `key` ("live", "m3u", ...) of a user."""
    seen = _observed.get((user_id, key))
    if seen is not None and seen[0] == rev.rev:
        return seen[1]
    data_lm = _norm(rev.updated_at)
    candidates = [data_lm.replace(microsecond=0)] if data_lm else []
    if seen is None:
        candidates.append(_PROCESS_START)
    else:
        # Strictly after the previous value, so a change within the same second as the last
        # response can't be hidden by second-granularity If-Modified-Since.
        candidates.append(datetime.fromtimestamp(int(time.time()), timezone.utc))
        candidates.append(seen[1] + timedelta(seconds=1))
    lm = max(candidates)
    if len(_observed) >= _MAX_OBSERVED:
        _observed.clear()
    _observed[(user_id, key)] = (rev.rev, lm)
    return lm
