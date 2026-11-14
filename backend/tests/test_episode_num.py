from lxml import etree

from app.services.epg_parser import format_episode_num, parse_xmltv


def _nums(*pairs):
    out = []
    for system, text in pairs:
        el = etree.Element("episode-num")
        if system:
            el.set("system", system)
        el.text = text
        out.append(el)
    return out


def test_air_time_is_not_an_episode():
    assert format_episode_num(_nums(("original-air-date", "20261114113439 +0000"))) is None
    assert format_episode_num(_nums((None, "20261114113439 +0000"))) is None
    assert format_episode_num(_nums(("dd_progid", "EP00012345.0042"))) is None
    assert format_episode_num(_nums(("", "2026-11-14"))) == "2026-11-14"


def test_real_episode_still_wins_beside_an_air_time():
    pairs = (("original-air-date", "20261114113439 +0000"), ("xmltv_ns", "3.21.0/1"))
    assert format_episode_num(_nums(*pairs)) == "S4 E22"
    assert format_episode_num(_nums(("onscreen", "S04E22"))) == "S04E22"


def test_parser_leaves_no_episode_and_keeps_the_year():
    xml = b"""<tv><programme channel="a" start="20261114113439 +0000" stop="20261114120218 +0000">
    <title>Perfect Strangers</title><date>1989</date>
    <episode-num system="original-air-date">20261114113439 +0000</episode-num></programme></tv>"""
    _, programs = parse_xmltv(xml)
    assert programs[0].episode_num is None
    assert programs[0].program_date == "1989"


def test_movie_year_ignores_an_airing_timestamp():
    from types import SimpleNamespace

    from app.services.epg_writer import _layout_token_value

    def prog(date, ep=None):
        return SimpleNamespace(program_date=date, episode_num=ep)

    assert _layout_token_value("episode_or_year", prog("20261114113439 +0000"), None) == ""
    assert _layout_token_value("movie_year", prog("19890612"), None) == "1989"
    assert _layout_token_value("episode_or_year", prog("1989"), None) == "1989"
