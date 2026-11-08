"""The command against the real Jarvis SDK interface, with the service and the player replaced."""

import pytest

from commands.plex_play import command as mod
from commands.plex_play.command import PlexPlayCommand
from jarvis_command_sdk import IJarvisCommand
from plex_shared.music_common import Resolved, ServiceError


class FakeService:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def find(self, query, kind, artist):
        self.calls.append((query, kind, artist))
        if self.error:
            raise self.error
        return self.result


class FakePlayer:
    def __init__(self):
        self.played = []

    def play(self, item):
        self.played.append(item)


@pytest.fixture
def setup(monkeypatch):
    def build(result=None, error=None):
        cmd, svc, pl = PlexPlayCommand(), FakeService(result, error), FakePlayer()
        monkeypatch.setattr(cmd, "_service", lambda: svc)
        monkeypatch.setattr(mod, "PLAYER", pl)
        return cmd, svc, pl
    return build


class TestInterface:
    def test_is_a_jarvis_command(self):
        cmd = PlexPlayCommand()
        assert isinstance(cmd, IJarvisCommand) and cmd.command_name == "plex_play"
        assert [p.name for p in cmd.parameters] == ["query", "kind", "artist"]
        assert cmd.generate_adapter_examples() and cmd.generate_prompt_examples()

    def test_secrets(self):
        secrets = {s.key: s for s in PlexPlayCommand().required_secrets}
        assert set(secrets) == {"PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY"}
        assert {k for k, s in secrets.items() if s.is_sensitive} == {"PLEX_TOKEN"}


class TestFastPath:
    @pytest.fixture
    def cmd(self):
        return PlexPlayCommand()

    @pytest.mark.parametrize("said,args", [
        ("Play Hysteria by Muse on Plex", {"query": "Hysteria", "kind": "track", "artist": "Muse"}),
        ("play Taylor Swift on Plex.", {"query": "Taylor Swift"}),
        ("Plex, play the album Rumours", {"query": "Rumours", "kind": "album"}),
        ("Hey Jarvis, play some Tame Impala from Plex", {"query": "Tame Impala", "kind": "artist"}),
    ])
    def test_claims_and_every_argument_is_valid(self, cmd, said, args):
        got = cmd.pre_route(said).arguments
        assert got == args and all(r.success for r in cmd.validate_call(**got))

    @pytest.mark.parametrize("said", ["play some jazz", "play Korn on spotify", "what is playing on Plex", "play"])
    def test_leaves_everything_else_alone(self, cmd, said):
        assert cmd.pre_route(said) is None

    def test_a_switched_off_pattern_is_not_claimed(self, cmd):
        assert cmd.pre_route("play x on Plex", disabled_pattern_ids={"plex.play_on"}) is None


class TestRun:
    item = Resolved("track", "Hysteria", "Muse", ["http://a/1"])

    def test_finds_plays_and_confirms(self, setup):
        cmd, svc, pl = setup(self.item)
        r = cmd.run(None, query="Hysteria", kind="track", artist="Muse")
        assert r.context_data["message"] == "Playing Hysteria by Muse." and pl.played == [self.item]
        assert svc.calls == [("Hysteria", "track", "Muse")]

    def test_kind_defaults_to_auto(self, setup):
        cmd, svc, _ = setup(self.item)
        cmd.run(None, query="Muse")
        assert svc.calls[0][1] == "auto"

    def test_missing_query_asks(self, setup):
        assert "What would you like" in setup()[0].run(None).error_details

    def test_service_errors_are_spoken_and_nothing_plays(self, setup):
        cmd, _, pl = setup(error=ServiceError("I couldn't find x on Plex."))
        assert "couldn't find x" in cmd.run(None, query="x").error_details and not pl.played

    def test_unexpected_errors_are_contained(self, setup):
        cmd, _, _ = setup(error=RuntimeError("boom"))
        r = cmd.run(None, query="x")
        assert "didn't respond" in r.error_details and r.context_data["error"] == "unexpected"
