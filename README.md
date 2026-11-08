# jarvis-cmd-plex

A [Jarvis](https://github.com/alexberardi/jarvis-server) Pantry package that plays music from your own Plex Media Server through the node's speaker.

## What you can say

| You say                               | Plays                                |
| ------------------------------------- | ------------------------------------ |
| "Play Hysteria by Muse on Plex"       | The song                             |
| "Play the album Rumours from Plex"    | The album                            |
| "Play Taylor Swift on Plex"           | The artist (an exact name), shuffled |
| "Play my Chill Hits playlist on Plex" | The playlist                         |
| "Plex, play Discover Weekly"          | Best match                           |

The phrase must include "Plex". These are fast paths, so the node runs them with no model, and each can be switched off
in the app's Inspect fast-paths screen. With [jarvis-cmd-music](https://github.com/opticon454/jarvis-cmd-music), "play
Korn" also works and uses your default music service.

## Requirements

- A Plex Media Server with a music library. Plex Pass is not needed.
- `mpv` on the node.

## Settings

Entered in the Jarvis app; nothing is stored in this repository.

| Setting        | Required | Meaning                                            |
| -------------- | -------- | -------------------------------------------------- |
| `PLEX_URL`     | Yes      | Server address, e.g. `http://192.168.1.10:32400`.  |
| `PLEX_TOKEN`   | Yes      | Your Plex token. Sent as a header, never in a URL. |
| `PLEX_LIBRARY` | No       | Name of the music library to search. Default: all. |

## How it plays

The command finds the music, then starts `mpv` on the node with the stream addresses. A new play replaces the old one,
pauses the Spotify Connect receiver if it is running, and ends any other `mpv`, so two streams never share the speaker.
Audio goes to a connected Bluetooth speaker, else PulseAudio (so the agent can lower it while you talk), else the Pi
image's ALSA `output` alias. Long queues are limited to 50 tracks.

## Status

Tested against a fake Plex server shaped from the HTTP API. **Not yet run against a real server.**

## Tests

```bash
PYTHONPATH=/path/to/jarvis-command-sdk python3 -m pytest
```

The tests use fakes, so no node, account or network is needed.

## Licence

Apache-2.0. Not affiliated with Plex. `mpv` is a separate program and is not included.
