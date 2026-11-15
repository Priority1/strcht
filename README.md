# strcht

Recordings played as live streams, with a live translation console on top.

Every video in `MEDIA_DIR` (default `~/Downloads/Mayflower`) is on air as a looping **live HLS** stream —
1 s MPEG-TS segments, `PROGRAM-DATE-TIME`, two variants — with thumbnails. `GET /pinhole/list` is the
**beacon**: the list of streams on air, in the shape a live platform hands to its integrations. Palabra's
translation console reads its streams from such a beacon, so it runs on top of the recordings exactly as it
runs on top of real live streams: same code, one variable changed.

```
MEDIA_DIR/*.mp4 ──ffmpeg, once──▶ DATA_DIR/<id>/{lo,hi}/00000.ts …  thumbs/00001.jpg …
                                        │
            wall clock ──▶ live playlists (nothing runs per stream; every client sees the same live edge)
                                        │
   /pinhole/list ──▶ the console ──▶ browser plays masterUrl · server taps the lowest variant ──▶ Palabra S2S
   /             ──▶ our own wall: the frame on air now, live preview on hover, player with a storyboard on the seek bar
```

## Run

```bash
./run.sh                  # strcht                         http://127.0.0.1:8790
./scripts/console.sh      # the console on top of it       http://127.0.0.1:8788
```

`run.sh` needs `ffmpeg` and Python 3 (developed on 3.14; it makes `.venv` on the first start). Settings go in
`.env` (see `.env.example`); without one, the defaults stream `~/Downloads/Mayflower` on `127.0.0.1:8790`.

The first start builds the streams in the background, in this order: thumbnails for every video (seconds), the
`lo` rendition for every video (a stream is **on air** from here), then `hi`. Measured for the 13 Mayflower
recordings (4.2 h) on an M1 Max: every thumbnail after 27 s, the twelve ~15 min recordings on air after 6 min, the
66 min one after 12 min, `hi` for all of them after 28 min (the wall shows the progress); `data/` is 7.5 GB for
6.1 GB of video. Later starts take what is there and are on air at once; a file that changed, or a setting that
changes the cut, rebuilds only what it affects. New and removed files are noticed within `RESCAN_SECONDS`.

On a server: [DEPLOY.md](DEPLOY.md) (one container, two volumes, one port).

## The console on top (`scripts/console.sh`)

The console is another project. It is run from its own checkout and **nothing in that checkout is changed**: its
`.env` (Palabra key, targets, site password, …) is used as it is, and the same console can keep running
elsewhere on its usual beacon. Which console, and how it is told where the beacon is, is local configuration
(`.env`, not in git):

| | |
|---|---|
| `CONSOLE_DIR` | the console's checkout |
| `CONSOLE_LIST_VAR` | the name of the console's own variable for the beacon url; the script sets it to `http://127.0.0.1:8790/pinhole/list` |
| `CONSOLE_TOKEN_VAR` | the name of its variable for the beacon's bearer token (our `PINHOLE_TOKEN`, when set) |
| `CONSOLE_HIDE` | names the console shows that this demo must not, comma separated |

**The skin (`console/skin.py`).** A console written for one customer names that customer: in page titles, the
header, tooltips, the sign-in prompt, its log lines, sometimes in a list of options. The script does not start
the console's app directly but our ASGI layer around it. For every name in `CONSOLE_HIDE`, whatever its case:

- pages, scripts and styles are rewritten on their way out (`Name's` → `the source's`, `Name` → `the source`,
  `NAME` → `LIVE`, `name` → `live`);
- JSON files the pages load lose the options that *are* that name (a model the viewer could pick in ⚙ config);
- the same for the `WWW-Authenticate` realm of the sign-in prompt and for everything the console prints.

API responses and WebSocket frames pass untouched — they carry data (our streams, transcripts), not wording.
`tests/test_names.py` fails when a file of this repository contains one of the names.

Other knobs of the script:

- **Source language.** The console's `.env` pins one; the recordings are in seven, often two per stream. So the
  script asks for detection among the languages `description.txt` names (they come with the beacon records as
  `languages`). `SOURCE_LANG=ru ./scripts/console.sh` pins one again; in the player, ⚙ config changes it per
  session.
- **Token.** Set `PINHOLE_TOKEN` in our `.env` and the beacon wants `authorization: bearer …`; the script hands
  the same token to the console.
- **Other machines.** Both listen on `127.0.0.1`. To open the console from elsewhere: `HOST=0.0.0.0` and
  `PUBLIC_URL=http://<this-host>:8790` for strcht (the browser fetches the video from it directly),
  `CONSOLE_HOST=0.0.0.0` for the console. `CONSOLE_PORT` moves it off `8788`.

Checked end to end with the console's own headless viewer and in its player: picture straight from strcht 3.0 s
behind the live edge (`target buffer 3.0s · stock player 3.0s`), tap `live` on the newest segment with
`wall − PDT` +0.2 s, transcripts, translations and voice from Palabra, no tap errors.

## What a stream is

A recording played in a loop on a fixed clock (`live.py`). Loop `L` of a stream starts at
`epoch + L × duration`; segment `i` is listed from the moment its last sample "has been captured", like a
segment of a live encoder. Every request computes the playlist from the current time:

| | |
|---|---|
| media sequence | `L × n + i` (`n` segments per loop) — never repeats, never goes back |
| `EXT-X-PROGRAM-DATE-TIME` | `epoch + L × duration + start of segment i`, on every segment |
| `EXT-X-DISCONTINUITY` | before segment 0 of every loop (the recording's timestamps restart); `EXT-X-DISCONTINUITY-SEQUENCE` = `L` |
| window | the last `LIVE_WINDOW_SEGMENTS` (12) complete segments |

So there is no encoder and no state per stream: players, the console's tap and a restarted server all agree on
the live edge, and a stream costs nothing while nobody watches. `epoch` is 2026-01-01T00:00Z plus an offset
derived from the stream's name, so the streams are not in step with each other.

**Renditions.** Both carry the source's audio as a stream copy — what a tap hears is the recording, not a
re-encode.

- `lo` — `LO_HEIGHT` (360), ≤ 30 fps, ~0.5 Mbit/s, libx264. The beacon's `previewUrl`; the lowest variant of
  the master, which is the one the console taps.
- `hi` — the source's size and frame rate. `HI_MODE=encode` (default): re-encoded with a keyframe every
  `SEGMENT_SECONDS` (VideoToolbox when there is one, else libx264) — exactly 1.000 s segments,
  `EXT-X-TARGETDURATION:1`, what low-latency live platforms cut. `HI_MODE=copy`: the source's packets untouched
  and available in a second, but a segment is one source GOP — 2 s for these files — which the console's 3 s
  picture buffer was not tuned for.

Both variants are cut on the same clock, so `PROGRAM-DATE-TIME` means the same picture in either: the console
aligns the voice (tapped from `lo`) with the picture (played from `hi`) through it.

## Endpoints

| | |
|---|---|
| `GET /pinhole/list` | the beacon: `[{streamId, userName, masterUrl, previewUrl, ipAddress, port, …}]` — streams on air. Ours on top: `thumbnailUrl`, `posterUrl`, `durationSeconds`, `positionSeconds`, `loop`, `languages`, `notes` |
| `GET /hls/{id}/master.m3u8` | live master (`hi`, `lo`) |
| `GET /hls/{id}/{lo\|hi}/live.m3u8` | live media playlist |
| `GET /hls/{id}/vod.m3u8`, `…/{lo\|hi}/vod.m3u8` | the recording as a seekable playlist (same segments) |
| `GET /hls/{id}/{lo\|hi}/00042.ts` | a segment (cached forever: the `?v=` in the playlists changes when a rebuild changes it) |
| `GET /thumbs/{id}/live.jpg` | the storyboard frame for what is on air now |
| `GET /thumbs/{id}/poster.jpg`, `…/00001.jpg` | a frame from 10 % into the video · storyboard frame *n* (one per `THUMB_EVERY_SECONDS`) |
| `GET /thumbs/{id}/storyboard.json`, `….vtt` | the frames with their times · the same as a WebVTT thumbnail track for a player's seek bar |
| `GET /api/streams`, `/api/streams/{id}` | everything above plus renditions and build progress |
| `GET /`, `/watch.html?id=` | the wall · the player (LIVE / RECORDING, storyboard on the bar) |
| `GET /healthz` | `ok` |

CORS is open (`*`): the console's page plays our HLS from another origin, as it would play a platform's CDN.

## Files

```
app.py              routes
live.py             playlists from the wall clock (pure functions)
library.py          scan MEDIA_DIR, ffmpeg jobs, DATA_DIR/<id>/meta.json, description.txt
config.py           settings
public/             the wall and the player (hls.js)
console/skin.py     the layer around the console: names it must not show
scripts/console.sh  start the console on top
tests/              pytest: the playlist over several loops; the whole service on generated clips; the skin (make test)
Dockerfile          the image · DEPLOY.md: how to run it on a server
```

## Notes

- `description.txt` in `MEDIA_DIR` (`Name:` + `- note` lines) gives every stream its notes and languages; a
  name is matched to the closest file name (`Nova_Kitchen` → `Nova_Kitchin.mp4`).
- A stream id is the file name in lower case with `_` for anything else (`River_Talk.mp4` → `river_talk`).
- ffmpeg as an HLS *client* prints `Packet corrupt` once per loop, at the wrap: the MPEG-TS continuity counters
  restart there, the playlist says so (`EXT-X-DISCONTINUITY`), ffmpeg's demuxer does not reset on it. No frame
  is lost; hls.js and the console's tap (which opens every segment on its own) are not affected.
- `data/` of a video that was removed is kept (it comes back for free); delete the directory to reclaim it.
