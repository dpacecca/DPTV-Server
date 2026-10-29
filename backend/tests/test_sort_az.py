from types import SimpleNamespace

from app.api.routes.playlists import sort_az_slots


def pc(cid: int, name: str, order: int, cat: int = 1):
    return SimpleNamespace(id=cid, name=name, sort_order=order, playlist_category_id=cat)


def test_selected_channels_sort_into_the_places_they_hold():
    channels = [pc(1, "Sky Sports 10", 2), pc(2, "BBC One", 5), pc(3, "sky sports 2", 9)]
    assert [(c.id, o) for c, o in sort_az_slots(channels)] == [(2, 2), (3, 5), (1, 9)]


def test_each_category_sorts_on_its_own():
    channels = [pc(1, "Zed", 0, cat=1), pc(2, "Alpha", 1, cat=1), pc(3, "Mid", 4, cat=2)]
    assert sorted((c.id, o) for c, o in sort_az_slots(channels)) == [(1, 1), (2, 0), (3, 4)]
