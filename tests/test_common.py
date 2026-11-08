"""The shared module: reading phrases, choosing a result, and building the mpv command."""

import subprocess

import pytest

from plex_shared.music_common import (AUTO, MAX_ITEMS, NodePlayer, Resolved, ServiceError, fast_arguments, fast_regexes,
                                       parse_request, pick, stop_other_players)
import re


@pytest.mark.parametrize("said,expected", [
    ("Hysteria by Muse", ("Hysteria", "track", "Muse")),
    ("the album Rumours", ("Rumours", "album", None)),
    ("my Chill Hits playlist", ("Chill Hits", "playlist", None)),
    ("some Tame Impala", ("Tame Impala", "artist", None)),
    ("the song Twist by Korn", ("Twist", "track", "Korn")),
    ("Taylor Swift", ("Taylor Swift", AUTO, None)),
    ("the album Abbey Road by The Beatles", ("Abbey Road", "album", "The Beatles")),
    ("my Songs by Me playlist", ("Songs by Me", "playlist", None)),
])
def test_parse_request(said, expected):
    assert parse_request(said) == expected


def test_fast_arguments_leave_kind_out_when_unsure():
    assert fast_arguments("Taylor Swift") == {"query": "Taylor Swift"}
    assert fast_arguments("Hysteria by Muse") == {"query": "Hysteria", "kind": "track", "artist": "Muse"}


def test_fast_regexes_need_the_service_name():
    play_on, first = (re.compile(r, re.IGNORECASE) for r in fast_regexes(r"foo"))
    assert play_on.search("Hey Jarvis, could you please play Korn on foo.").group("what") == "Korn"
    assert first.search("foo, play Korn").group("what") == "Korn"
    assert not play_on.search("play Korn") and not first.search("play Korn on spotify")


def test_pick_prefers_an_exact_artist_then_the_top_track():
    found = {"track": [{"name": "Muse"}], "artist": [{"name": "Muse"}], "album": [], "playlist": []}
    assert pick(found, "muse", AUTO)[0] == "artist"
    assert pick({"track": [{"name": "Hysteria"}], "artist": [{"name": "Muse"}]}, "hyst", AUTO)[0] == "track"
    assert pick({"track": [{"name": "x"}]}, "x", "album") is None
    assert pick({}, "x", AUTO) is None


class Proc:
    def __init__(self, code=None):
        self.code, self.killed, self.terminated = code, False, False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 0

    def wait(self, timeout=None):
        return self.code

    def kill(self):
        self.killed = True


def player(proc=None, mpv=True, pulse=True, alsa=False, bt=None):
    made = []

    def popen(cmd, **kw):
        made.append((cmd, kw))
        return proc or Proc()

    p = NodePlayer(popen=popen, which=lambda b: "/usr/bin/mpv" if mpv else None, sleep=lambda s: None,
                   pulse_ok=lambda: pulse, alsa_alias=lambda: alsa, bluetooth_sink=lambda: bt)
    return p, made


@pytest.fixture(autouse=True)
def no_takeover(monkeypatch):
    monkeypatch.setattr("plex_shared.music_common.stop_other_players", lambda *a, **k: None)


class TestNodePlayer:
    def item(self, **kw):
        return Resolved("track", "Hysteria", "Muse", kw.pop("urls", ["http://a/1"]), **kw)

    def test_builds_a_quiet_audio_only_mpv_with_the_urls_last(self):
        p, made = player()
        p.play(self.item(urls=["http://a/1", "http://a/2"], headers={"X-T": "k"}, mpv_args=["--ytdl-format=bestaudio"], shuffle=True))
        cmd = made[0][0]
        assert cmd[:4] == ["mpv", "--no-video", "--really-quiet", "--no-terminal"]
        assert "--shuffle" in cmd and "--http-header-fields=X-T: k" in cmd and "--ytdl-format=bestaudio" in cmd
        assert cmd[-3:] == ["--", "http://a/1", "http://a/2"]

    def test_caps_a_long_queue(self):
        p, made = player()
        p.play(self.item(urls=[f"http://a/{i}" for i in range(MAX_ITEMS + 30)]))
        assert len(made[0][0]) - made[0][0].index("--") - 1 == MAX_ITEMS

    def test_routing(self):
        assert "--ao=alsa" in player(pulse=False, alsa=True)[0].command(self.item())[0]
        assert "--ao=alsa" not in player(pulse=True, alsa=True)[0].command(self.item())[0]
        cmd, env = player(pulse=False, alsa=True, bt="bluez_sink.X")[0].command(self.item())
        assert "--ao=alsa" not in cmd and env["PULSE_SINK"] == "bluez_sink.X"

    def test_a_second_play_stops_the_first(self):
        first = Proc()
        p, made = player(proc=first)
        p.play(self.item())
        p._popen = lambda cmd, **kw: Proc()
        p.play(self.item())
        assert first.terminated

    def test_no_mpv_nothing_to_play_and_immediate_failure_are_spoken(self):
        with pytest.raises(ServiceError, match="Install mpv"):
            player(mpv=False)[0].play(self.item())
        with pytest.raises(ServiceError, match="nothing to play"):
            player()[0].play(self.item(urls=[]))
        with pytest.raises(ServiceError, match="couldn't start"):
            player(proc=Proc(code=2))[0].play(self.item())


def test_stop_other_players_pauses_the_receiver_and_ends_mpv_and_never_raises():
    calls = []

    class Http:
        def post(self, url, **kw):
            calls.append(url)
            raise OSError("no receiver")

    ran = []
    stop_other_players("http://x:1", run=lambda cmd, **kw: ran.append(cmd), http=Http())
    assert calls == ["http://x:1/player/pause"] and ran == [["pkill", "-INT", "-x", "mpv"]]
    stop_other_players(run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()), http=Http())
