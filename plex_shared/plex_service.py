"""Find music on a Plex Media Server and turn it into stream URLs for mpv.

Official HTTP API, JSON. The token travels as the X-Plex-Token header (never in a URL), and mpv sends the same header.
"""

from __future__ import annotations

from typing import Any, Optional

from plex_shared.music_common import AUTO, KINDS, Resolved, ServiceError, pick

_TIMEOUT = 10
_HUB_KINDS = {"artist": "artist", "album": "album", "track": "track", "playlist": "playlist"}


class PlexService:
    def __init__(self, http: Any, base_url: str, token: str, library: str = "") -> None:
        self._http = http
        self._base = base_url.rstrip("/")
        self._headers = {"X-Plex-Token": token, "Accept": "application/json"}
        self._library = library.strip()

    def _get(self, path: str, **params: Any) -> dict:
        try:
            r = self._http.get(self._base + path, params=params or None, headers=self._headers, timeout=_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - requests' errors differ by version
            raise ServiceError("I can't reach your Plex server.") from exc
        if r.status_code == 401:
            raise ServiceError("Plex rejected the token. Check it in the app.")
        if r.status_code != 200:
            raise ServiceError("Plex didn't answer that. Try again in a moment.")
        return r.json().get("MediaContainer") or {}

    def _section_id(self) -> Optional[str]:
        if not self._library:
            return None
        for d in self._get("/library/sections").get("Directory") or []:
            if d.get("title", "").casefold() == self._library.casefold():
                return str(d["key"])
        raise ServiceError(f"There's no Plex library called {self._library}.")

    def find(self, query: str, kind: str = AUTO, artist: Optional[str] = None) -> Resolved:
        kind = kind if kind in KINDS else AUTO
        params: dict[str, Any] = {"query": query, "limit": 10}
        section = self._section_id()
        if section:
            params["sectionId"] = section
        found: dict[str, list] = {}
        for hub in self._get("/hubs/search", **params).get("Hub") or []:
            k = _HUB_KINDS.get(hub.get("type", ""))
            if k:
                found[k] = [m for m in hub.get("Metadata") or [] if m.get("ratingKey")]
        if artist:  # prefer the song or album by that artist; keep the rest as a fallback
            for k in ("track", "album"):
                found[k] = sorted(found.get(k, []), key=lambda m: artist.casefold() not in _by(m).casefold())
        hit = pick(found, query, kind)
        if hit is None:
            raise ServiceError(f"I couldn't find {query} on Plex.")
        k, item = hit
        urls = self._urls(k, item)
        if not urls:
            raise ServiceError(f"{item.get('title', query)} has nothing playable on Plex.")
        return Resolved(k, item.get("title", query), _by(item) if k != "playlist" else "", urls, headers={"X-Plex-Token": self._headers["X-Plex-Token"]},
                        shuffle=k == "artist")

    def _urls(self, kind: str, item: dict) -> list[str]:
        rk = item["ratingKey"]
        if kind == "track":
            tracks = [item]
        elif kind == "album":
            tracks = self._get(f"/library/metadata/{rk}/children").get("Metadata") or []
        elif kind == "artist":
            tracks = self._get(f"/library/metadata/{rk}/allLeaves").get("Metadata") or []
        else:
            tracks = self._get(f"/playlists/{rk}/items").get("Metadata") or []
        return [u for u in (self._part_url(t) for t in tracks) if u]

    def _part_url(self, track: dict) -> Optional[str]:
        media = track.get("Media")
        if media is None:  # search hits can omit the file; the track's own record has it
            media = self._get(f"/library/metadata/{track['ratingKey']}").get("Metadata", [{}])[0].get("Media")
        try:
            return self._base + media[0]["Part"][0]["key"]
        except (TypeError, KeyError, IndexError):
            return None


def _by(item: dict) -> str:
    return item.get("grandparentTitle") or item.get("parentTitle") or ""
