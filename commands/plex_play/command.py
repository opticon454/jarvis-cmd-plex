"""PlexPlayCommand: "play <something> on Plex" through this node's speaker (mpv)."""

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

for _candidate in ("plex_shared", "lib"):
    _lib_dir: str = str(Path.home() / ".jarvis" / "packages" / "plex_play" / _candidate)
    if _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)

try:
    from jarvis_log_client import JarvisLogger
except ImportError:
    import logging

    class JarvisLogger:  # noqa: E303
        def __init__(self, **kw: str) -> None:
            self._log = logging.getLogger(kw.get("service", __name__))

        def error(self, msg: str, **kw: object) -> None: self._log.error(msg)

from jarvis_command_sdk import (
    CommandExample,
    CommandResponse,
    FastPathPattern,
    IJarvisCommand,
    IJarvisSecret,
    JarvisPackage,
    JarvisParameter,
    JarvisSecret,
    JarvisStorage,
    RequestInformation,
)
from jarvis_command_sdk.command import PreRouteResult

from plex_shared.music_common import AUTO, KINDS, PLAYER, ServiceError, fast_arguments, fast_regexes
from plex_shared.plex_service import PlexService

logger = JarvisLogger(service="plex-play")
_PLAY_ON, _NAME_FIRST = fast_regexes(r"plex")


class PlexPlayCommand(IJarvisCommand):
    """Voice search and play on Plex."""

    def __init__(self) -> None:
        self._storage = JarvisStorage("plex_play")

    @property
    def command_name(self) -> str:
        return "plex_play"

    @property
    def description(self) -> str:
        return (
            "Play a song, album, artist or playlist from Plex. query is what to play, kind says what it is "
            "(leave empty unless the user says song, album, artist or playlist), artist narrows a song or album. "
            "Use when the user names Plex: 'play ... on Plex'."
        )

    @property
    def associated_service(self) -> Optional[str]:
        return "Plex"

    @property
    def keywords(self) -> List[str]:
        return ["plex", "play on plex", "play from plex", "song", "album", "playlist"]

    @property
    def parameters(self) -> List[JarvisParameter]:
        return [
            JarvisParameter("query", "string", required=True,
                            description="What to play: a song, album, artist or playlist name, without the service"),
            JarvisParameter("kind", "string", required=False, enum_values=KINDS,
                            description="track, album, artist or playlist. Leave empty when unsure."),
            JarvisParameter("artist", "string", required=False,
                            description="The artist, when the user named one for a song or album"),
        ]

    @property
    def fast_path_patterns(self) -> List[FastPathPattern]:
        return [
            FastPathPattern(id="plex.play_on", description="Play something when you say 'on Plex'",
                            example="Play Hysteria by Muse on Plex", regex=_PLAY_ON, handler="_fast_play"),
            FastPathPattern(id="plex.plex_play", description="Play something when the phrase starts with 'Plex'",
                            example="Plex, play Discover Weekly", regex=_NAME_FIRST, handler="_fast_play"),
        ]

    def _fast_play(self, match: "re.Match[str]", voice_command: str) -> Optional[PreRouteResult]:
        args = fast_arguments(match.group("what"))
        return PreRouteResult(arguments=args) if args else None

    @property
    def required_secrets(self) -> List[IJarvisSecret]:
        return [
            JarvisSecret("PLEX_URL", "Address of your Plex server, e.g. http://192.168.1.10:32400", "integration", "string", is_sensitive=False, friendly_name="Server URL"),
            JarvisSecret("PLEX_TOKEN", "Your Plex token (X-Plex-Token)", "integration", "string", friendly_name="Plex token"),
            JarvisSecret("PLEX_LIBRARY", "Name of the music library to search (default: all)", "integration", "string", required=False, is_sensitive=False, friendly_name="Music library"),
        ]

    @property
    def required_packages(self) -> List[JarvisPackage]:
        return [JarvisPackage("requests", ">=2.31.0")]

    @property
    def setup_guide(self) -> Optional[str]:
        return (
            '## Connect Plex\n'
            '\n'
            "Plays music from your own Plex Media Server through this node's speaker. Plex Pass is not needed.\n"
            '\n'
            '1. Enter the server address, for example `http://192.168.1.10:32400`.\n'
            '2. Find your token: in Plex Web open any item, choose Get Info, then View XML; the address ends with `X-Plex-Token=...`. See https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/\n'
            '3. Optionally name the music library to search. Leave it empty to search every library.\n'
            '4. The node needs `mpv` installed.\n'
        )

    def generate_prompt_examples(self) -> List[CommandExample]:
        return [
            CommandExample("Play Hysteria by Muse on Plex",
                           {"query": "Hysteria", "kind": "track", "artist": "Muse"}, is_primary=True),
            CommandExample("Play the album Black Holes and Revelations on Plex",
                           {"query": "Black Holes and Revelations", "kind": "album"}),
            CommandExample("Play some Muse from Plex", {"query": "Muse", "kind": "artist"}),
        ]

    def generate_adapter_examples(self) -> List[CommandExample]:
        rows = [
            ("Play Bohemian Rhapsody on Plex", "Bohemian Rhapsody", "track", None),
            ("Play Thunderstruck by AC/DC from Plex", "Thunderstruck", "track", "AC/DC"),
            ("Play the album Rumours on Plex", "Rumours", "album", None),
            ("Play Abbey Road by The Beatles on Plex", "Abbey Road", "album", "The Beatles"),
            ("Play Taylor Swift on Plex", "Taylor Swift", "artist", None),
            ("Play my Chill Hits playlist on Plex", "Chill Hits", "playlist", None),
            ("Plex, play Running Up That Hill", "Running Up That Hill", "track", None),
        ]
        out = []
        for text, query, kind, artist in rows:
            params = {"query": query, "kind": kind}
            if artist:
                params["artist"] = artist
            out.append(CommandExample(text, params))
        return out

    def _service(self) -> PlexService:
        import requests

        url = self._storage.get_secret("PLEX_URL")
        token = self._storage.get_secret("PLEX_TOKEN")
        if not (url and token):
            raise ServiceError("Plex isn't set up yet. Add the server address and token in the app.")
        return PlexService(requests, url, token, self._storage.get_secret("PLEX_LIBRARY") or "")

    def run(self, request_info: RequestInformation, **kwargs: Any) -> CommandResponse:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return CommandResponse.error_response(error_details="What would you like me to play?",
                                                  context_data={"error": "missing_query"})
        kind = str(kwargs.get("kind") or AUTO).lower()
        try:
            item = self._service().find(query, kind, kwargs.get("artist"))
            PLAYER.play(item)
        except ServiceError as exc:
            return CommandResponse.error_response(error_details=str(exc), context_data={"error": "service_error"})
        except Exception as exc:  # noqa: BLE001 - never raise into the voice pipeline
            logger.error("Plex play failed", error=str(exc))
            return CommandResponse.error_response(error_details="Plex didn't respond. Try again in a moment.",
                                                  context_data={"error": "unexpected", "detail": str(exc)})
        return CommandResponse.success_response(
            context_data={"message": f"Playing {item.spoken()}.", "kind": item.kind}, wait_for_input=False)
