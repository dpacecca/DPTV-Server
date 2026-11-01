from app.services.duplicate_scanner import group_duplicates


def ch(cid: int, name: str, height: int = 1080) -> dict:
    return {"channel_id": cid, "name": name, "status": "ok", "height": height, "fps": 50, "bitrate_kbps": 5000}


def test_the_same_channel_at_two_qualities_is_one_group():
    groups = group_duplicates([ch(1, "UK: SKY SPORTS MAIN EVENT ⁴ᵏ & ³⁸⁴⁰ᴾ", 2160), ch(2, "UK: SKY SPORTS MAIN EVENT ᴿᴬᵂ")])
    assert [g["channel_ids"] for g in groups] == [[1, 2]]
    assert groups[0]["best_channel_id"] == 1


def test_different_countries_stay_apart_when_asked():
    channels = [ch(1, "AU: ESPN 2 HD"), ch(2, "US: ESPN 2 HD")]
    assert len(group_duplicates(channels)) == 1
    assert group_duplicates(channels, split_countries=True) == []


def test_one_country_with_copies_is_still_a_group_when_split():
    channels = [ch(1, "AU: ESPN 2 HD"), ch(2, "US: ESPN 2 HD"), ch(3, "US: ESPN 2 RAW", 720)]
    groups = group_duplicates(channels, split_countries=True)
    assert [(g["key"], g["channel_ids"]) for g in groups] == [("espn 2 (US)", [2, 3])]


def test_a_shared_country_or_no_country_is_not_split():
    channels = [ch(1, "US: BEIN SPORTS 2 HD"), ch(2, "PRIME: BEIN SPORTS 2 ᴿᴬᵂ", 720)]
    assert [g["channel_ids"] for g in group_duplicates(channels, split_countries=True)] == [[1, 2]]
