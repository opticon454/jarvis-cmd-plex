import pytest

from plex_shared.music_common import ServiceError
from plex_shared.plex_service import PlexService

TRACK = {"ratingKey": "1", "title": "Hysteria", "grandparentTitle": "Muse", "parentTitle": "Absolution",
         "Media": [{"Part": [{"key": "/library/parts/10/file.flac"}]}]}
OTHER = {"ratingKey": "2", "title": "Hysteria", "grandparentTitle": "Def Leppard", "Media": [{"Part": [{"key": "/library/parts/11/f.mp3"}]}]}


class Resp:
    def __init__(self, payload, status=200):
        self.status_code, self._p = status, payload

    def json(self):
        return {"MediaContainer": self._p}


class Http:
    """Answers by path; records (path, params, headers)."""

    def __init__(self, routes, status=200):
        self.routes, self.status, self.calls = routes, status, []

    def get(self, url, params=None, headers=None, timeout=None):
        path = url.split("http://plex:32400")[1]
        self.calls.append((path, params, headers))
        if self.status != 200:
            return Resp({}, self.status)
        return Resp(self.routes[path])


def hubs(**kinds):
    return {"/hubs/search": {"Hub": [{"type": k, "Metadata": v} for k, v in kinds.items()]}}


def svc(routes, **kw):
    http = Http(routes, **kw)
    return PlexService(http, "http://plex:32400/", "TOK"), http


def test_track_uses_the_artist_to_choose_and_sends_the_token_as_a_header():
    s, http = svc(hubs(track=[OTHER, TRACK]))
    r = s.find("Hysteria", "track", "Muse")
    assert (r.kind, r.spoken(), r.urls) == ("track", "Hysteria by Muse", ["http://plex:32400/library/parts/10/file.flac"])
    assert r.headers == {"X-Plex-Token": "TOK"} and "TOK" not in r.urls[0]
    assert http.calls[0][2]["X-Plex-Token"] == "TOK" and http.calls[0][1]["query"] == "Hysteria"


def test_auto_prefers_an_exact_artist_and_shuffles_all_their_tracks():
    routes = hubs(track=[{**TRACK, "title": "Muse"}], artist=[{"ratingKey": "9", "title": "Muse"}])
    routes["/library/metadata/9/allLeaves"] = {"Metadata": [TRACK, OTHER]}
    r = svc(routes)[0].find("muse")
    assert r.kind == "artist" and r.shuffle and len(r.urls) == 2


def test_album_and_playlist_expand_to_their_tracks_in_order():
    routes = hubs(album=[{"ratingKey": "5", "title": "Absolution", "parentTitle": "Muse"}], playlist=[{"ratingKey": "6", "title": "Mix"}])
    routes["/library/metadata/5/children"] = {"Metadata": [TRACK, OTHER]}
    routes["/playlists/6/items"] = {"Metadata": [OTHER]}
    a = svc(routes)[0].find("Absolution", "album")
    assert a.urls[0].endswith("10/file.flac") and a.urls[1].endswith("11/f.mp3") and not a.shuffle
    assert svc(routes)[0].find("Mix", "playlist").urls[0].endswith("11/f.mp3")


def test_a_search_hit_without_its_file_is_looked_up():
    bare = {"ratingKey": "1", "title": "Hysteria", "grandparentTitle": "Muse"}
    routes = hubs(track=[bare])
    routes["/library/metadata/1"] = {"Metadata": [TRACK]}
    assert svc(routes)[0].find("Hysteria", "track").urls == ["http://plex:32400/library/parts/10/file.flac"]


def test_a_named_library_limits_the_search_and_an_unknown_one_is_spoken():
    routes = hubs(track=[TRACK])
    routes["/library/sections"] = {"Directory": [{"title": "Music", "key": "3"}]}
    http = Http(routes)
    PlexService(http, "http://plex:32400", "T", "music").find("x", "track")
    assert http.calls[-1][1]["sectionId"] == "3"
    with pytest.raises(ServiceError, match="no Plex library called Films"):
        PlexService(Http(routes), "http://plex:32400", "T", "Films").find("x")


@pytest.mark.parametrize("status,text", [(401, "rejected the token"), (500, "didn't answer")])
def test_failures_are_speakable(status, text):
    with pytest.raises(ServiceError, match=text):
        svc({}, status=status)[0].find("x")


def test_nothing_found_and_nothing_playable():
    with pytest.raises(ServiceError, match="couldn't find Nope on Plex"):
        svc(hubs(track=[]))[0].find("Nope")
    with pytest.raises(ServiceError, match="nothing playable"):
        svc(hubs(track=[{"ratingKey": "1", "title": "Ghost", "Media": []}]))[0].find("Ghost", "track")


def test_unreachable_server_is_spoken():
    class Down:
        def get(self, *a, **k):
            raise OSError("refused")

    with pytest.raises(ServiceError, match="can't reach your Plex"):
        PlexService(Down(), "http://plex:32400", "T").find("x")
