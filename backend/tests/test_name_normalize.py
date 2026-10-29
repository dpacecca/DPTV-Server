import pytest

from app.services.name_normalize import extract_country_prefix, normalize_name


@pytest.mark.parametrize(
    "raw, normalized, country",
    [
        # A provider's superscript tags go, whatever sits between them.
        ("UK: SKY SPORTS MAIN EVENT ⁴ᵏ & ³⁸⁴⁰ᴾ", "sky sports main event", "UK"),
        # Bar and bracket prefixes are country prefixes too.
        ("UK| SKY SPORTS MAIN EVENT ⁴ᵏ", "sky sports main event", "UK"),
        ("DE | Sky Cinema ᴴᴰ", "sky cinema", "DE"),
        ("|UK| Sky One", "sky one", "UK"),
        ("[UK] Sky Sports F1 FHD", "sky sports f1", "UK"),
        ("(US) ESPN", "espn", "US"),
        # Spacing-modifier superscripts ("ʰᵈ") are tags as well.
        ("Sky Sports Main Event ʰᵈ", "sky sports main event", None),
        # A bar inside a name is not a prefix.
        ("Sky Sports | Main Event", "sky sports main event", None),
        ("BBC One", "bbc one", None),
    ],
)
def test_provider_decoration_is_ignored(raw, normalized, country):
    assert normalize_name(raw) == normalized
    assert extract_country_prefix(raw) == country


def test_the_same_channel_matches_across_decorations():
    names = ["UK: SKY SPORTS MAIN EVENT ⁴ᵏ & ³⁸⁴⁰ᴾ", "UK| Sky Sports Main Event HD", "[UK] Sky Sports Main Event ʰᵈ"]
    assert len({normalize_name(n) for n in names}) == 1
