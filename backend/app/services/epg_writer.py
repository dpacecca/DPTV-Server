from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import DummyEpgMode
from app.models.epg import EpgProgram
from app.models.playlist import Playlist, PlaylistChannel
from app.services import dummy_epg

DEFAULT_WINDOW_HOURS = 24 * 3


def _xmltv_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y%m%d%H%M%S %z")


def _resolve_dummy_mode(pc: PlaylistChannel) -> DummyEpgMode:
    if pc.dummy_epg_mode != DummyEpgMode.INHERIT:
        return pc.dummy_epg_mode
    if pc.category.dummy_epg_for_unassigned:
        return DummyEpgMode.NAME
    return DummyEpgMode.OFF


def _resolve_program_minutes(pc: PlaylistChannel) -> int:
    if pc.dummy_epg_program_minutes:
        return pc.dummy_epg_program_minutes
    return pc.category.dummy_epg_program_minutes


async def build_xmltv(db: AsyncSession, playlist: Playlist, window_hours: int = DEFAULT_WINDOW_HOURS) -> bytes:
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)

    channel_xml: list[str] = []
    programme_xml: list[str] = []

    all_channels: list[PlaylistChannel] = [
        pc for category in playlist.categories for pc in category.channels if pc.enabled
    ]

    real_epg_channel_ids = {pc.epg_channel_id for pc in all_channels if pc.epg_channel_id}
    programs_by_epg_channel: dict[int, list[EpgProgram]] = {}
    if real_epg_channel_ids:
        result = await db.execute(
            select(EpgProgram)
            .where(EpgProgram.epg_channel_id.in_(real_epg_channel_ids))
            .where(EpgProgram.stop > now)
            .where(EpgProgram.start < window_end)
            .order_by(EpgProgram.start)
        )
        for prog in result.scalars().all():
            programs_by_epg_channel.setdefault(prog.epg_channel_id, []).append(prog)

    for pc in all_channels:
        cid = f"pc{pc.id}"
        icon = pc.logo_url_override or (pc.source_channel.logo_url if pc.source_channel else None)
        icon_tag = f'<icon src="{escape(icon)}"/>' if icon else ""
        channel_xml.append(f'<channel id="{cid}"><display-name>{escape(pc.name)}</display-name>{icon_tag}</channel>')

        programs = programs_by_epg_channel.get(pc.epg_channel_id) if pc.epg_channel_id else None
        if programs:
            for prog in programs:
                desc = f"<desc>{escape(prog.description)}</desc>" if prog.description else ""
                programme_xml.append(
                    f'<programme start="{_xmltv_time(prog.start)}" stop="{_xmltv_time(prog.stop)}" channel="{cid}">'
                    f"<title>{escape(prog.title)}</title>{desc}</programme>"
                )
            continue

        mode = _resolve_dummy_mode(pc)
        if mode == DummyEpgMode.OFF:
            continue
        minutes = _resolve_program_minutes(pc)
        if mode == DummyEpgMode.EVENT:
            dummies = dummy_epg.generate_event_dummy(pc.name, now, window_hours, minutes)
        else:
            dummies = dummy_epg.generate_name_dummy(pc.name, now, window_hours, minutes)
        for d in dummies:
            programme_xml.append(
                f'<programme start="{_xmltv_time(d.start)}" stop="{_xmltv_time(d.stop)}" channel="{cid}">'
                f"<title>{escape(d.title)}</title></programme>"
            )

    body = "".join(channel_xml) + "".join(programme_xml)
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<tv generator-info-name="DPTV-Server">{body}</tv>\n'
    return xml.encode("utf-8")
