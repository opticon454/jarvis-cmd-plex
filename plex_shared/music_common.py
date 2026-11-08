"""Shared by the music service packages (plex, emby, youtube_music, tidal): one copy lives in each package's
``*_shared`` folder so each installs on its own. What is common: the spoken error type, what a search resolves to,
reading "play X by Y" phrases, and playing a list of stream URLs through mpv on the node.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

AUTO = "auto"  # the user did not say whether X is a song, album, artist or playlist
KINDS = ["track", "album", "artist", "playlist"]
MAX_ITEMS = 50  # a long queue makes a huge mpv command line and a slow start


class ServiceError(Exception):
    """A failure whose message is fit to speak."""


@dataclass
class Resolved:
    """What a search found, ready to play. ``urls`` are what mpv opens; ``headers`` go with every request."""

    kind: str
    name: str
    by: str  # the artist, or the playlist owner; may be empty
    urls: list[str]
    headers: dict[str, str] = field(default_factory=dict)
    mpv_args: list[str] = field(default_factory=list)
    shuffle: bool = False  # an artist's tracks are in catalogue order, so mix them

    def spoken(self) -> str:
        return f"{self.name} by {self.by}" if self.by and self.kind in ("track", "album") else self.name


def pick(found: dict[str, list], query: str, kind: str) -> Optional[tuple[str, Any]]:
    """The best (kind, item) from per-kind results. With AUTO an exact name wins, artists first ("play Muse" is
    the band, not a song); otherwise the top song, then artist, album, playlist."""
    if kind != AUTO:
        items = found.get(kind) or []
        return (kind, items[0]) if items else None
    want = query.strip().casefold()
    for k in ("artist", "track", "album", "playlist"):
        for item in found.get(k) or []:
            if _name(item).casefold() == want:
                return k, item
    for k in ("track", "artist", "album", "playlist"):
        if found.get(k):
            return k, found[k][0]
    return None


def _name(item: Any) -> str:
    return (item.get("name") or item.get("title") or item.get("Name") or "") if isinstance(item, dict) else str(getattr(item, "name", ""))


# --- reading what was said -------------------------------------------------------------------------------------------

_LEAD = r"^\s*(?:hey,?\s+jarvis,?\s+)?(?:(?:can|could|will)\s+you\s+)?(?:please\s+)?"
_VERB = r"(?:play|put\s+on)"


def fast_regexes(service: str) -> tuple[str, str]:
    """("play X on <service>", "<service>, play X") as regexes with a ``what`` group. ``service`` is a regex such as
    ``plex`` or ``you\\s*tube\\s+music``. Both need the service's name, so a bare "play" is left to the model."""
    return (_LEAD + _VERB + r"\s+(?P<what>.+?)\s+(?:on|from|in|using|with|via)\s+" + service + r"\W*$",
            _LEAD + service + r",?\s+" + _VERB + r"\s+(?P<what>.+?)\W*$")


def parse_request(what: str) -> tuple[str, str, Optional[str]]:
    """What the user said to play -> (query, kind, artist). "the album X by Y", "my X playlist", "some X" (an
    artist), "the song X by Y"; anything else is AUTO."""
    what = re.sub(r"\s+", " ", what).strip().strip("\"'")
    kind = AUTO
    for pattern, k in ((r"^(?:the\s+|my\s+)?album\s+(.+)$", "album"), (r"^(?:the\s+|my\s+)?playlist\s+(.+)$", "playlist"),
                       (r"^(?:the\s+|my\s+)?(.+?)\s+playlist$", "playlist"), (r"^(?:the\s+)?(?:song|track)\s+(.+)$", "track"),
                       (r"^(?:some|any)\s+(.+)$", "artist")):
        m = re.match(pattern, what, re.IGNORECASE)
        if m:
            what, kind = m.group(1).strip(), k
            break
    artist = None
    m = re.match(r"^(.+?)\s+by\s+(.+)$", what, re.IGNORECASE)
    if m and kind in (AUTO, "track", "album"):
        what, artist = m.group(1).strip(), m.group(2).strip()
        kind = "track" if kind == AUTO else kind
    return what, kind, artist


def fast_arguments(what: str) -> Optional[dict[str, Any]]:
    """Arguments for the command from a fast-path match, leaving ``kind`` out when unsure (the command's enum has
    no "auto", and the sidecar validates before it runs)."""
    query, kind, artist = parse_request(what)
    if not query:
        return None
    args: dict[str, Any] = {"query": query}
    if kind != AUTO:
        args["kind"] = kind
    if artist:
        args["artist"] = artist
    return args


# --- playing ---------------------------------------------------------------------------------------------------------


def _pulse_ok() -> bool:
    if shutil.which("pactl") is None:
        return False
    try:
        return subprocess.run(["pactl", "info"], capture_output=True, timeout=2).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _alsa_output_alias() -> bool:
    """jarvis-node-setup's Pi image defines an ALSA alias named "output" for the speaker."""
    try:
        return "pcm.output" in open("/etc/asound.conf").read()
    except OSError:
        return False


class NodePlayer:
    """One mpv at a time on this node. Routing follows the Pandora package: a connected Bluetooth speaker, else
    PulseAudio when it is reachable (so the agent can duck it), else the Pi image's ALSA "output" alias."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen, which: Callable[[str], Optional[str]] = shutil.which,
                 sleep: Callable[[float], None] = time.sleep, pulse_ok: Callable[[], bool] = _pulse_ok,
                 alsa_alias: Callable[[], bool] = _alsa_output_alias, bluetooth_sink: Callable[[], Optional[str]] | None = None) -> None:
        self._popen, self._which, self._sleep = popen, which, sleep
        self._pulse_ok, self._alsa_alias, self._bt = pulse_ok, alsa_alias, bluetooth_sink or _bluetooth_sink
        self._proc: Any = None

    def command(self, item: Resolved) -> tuple[list[str], dict[str, str]]:
        cmd = ["mpv", "--no-video", "--really-quiet", "--no-terminal"]
        sink = self._bt()
        env = dict(os.environ)
        if sink:
            env["PULSE_SINK"] = sink
        elif not self._pulse_ok() and self._alsa_alias():
            cmd += ["--ao=alsa", "--audio-device=alsa/output"]
        if item.shuffle:
            cmd.append("--shuffle")
        if item.headers:
            cmd.append("--http-header-fields=" + ",".join(f"{k}: {v}" for k, v in item.headers.items()))
        return cmd + item.mpv_args + ["--", *item.urls[:MAX_ITEMS]], env

    def play(self, item: Resolved) -> None:
        if not item.urls:
            raise ServiceError(f"There is nothing to play for {item.name}.")
        if not self._which("mpv"):
            raise ServiceError("This node has no audio player. Install mpv.")
        self.stop()
        stop_other_players()
        cmd, env = self.command(item)
        self._proc = self._popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        self._sleep(0.8)  # a bad URL or a missing audio device makes mpv exit at once
        if self._proc.poll() not in (None, 0):
            self._proc = None
            raise ServiceError("The audio player couldn't start that.")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001 - kill rather than leave a second stream running
                proc.kill()


def _bluetooth_sink() -> Optional[str]:
    try:
        from jarvis_command_sdk import BluetoothAudio

        return BluetoothAudio.target_sink()
    except Exception:  # noqa: BLE001 - no SDK helper or no speaker
        return None


def stop_other_players(librespot_url: str = "http://127.0.0.1:3678", run: Callable[..., Any] = subprocess.run,
                       http: Any = None) -> None:
    """Two streams must not share the speaker: pause the Spotify Connect receiver and end any other mpv. The agent's
    own takeover only fires for commands with an ``action`` argument, so each service does this itself. Best effort."""
    try:
        if http is None:
            import requests as http  # type: ignore[no-redef]
        http.post(f"{librespot_url}/player/pause", timeout=1)
    except Exception:  # noqa: BLE001 - no receiver on this node
        pass
    try:
        run(["pkill", "-INT", "-x", "mpv"], capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


PLAYER = NodePlayer()
