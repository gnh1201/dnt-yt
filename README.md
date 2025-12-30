# DNT-YT

[![Discord chat](https://img.shields.io/discord/359930650330923008?logo=discord)](https://discord.gg/sZPCYDGWGM?utm_source=gnh1201)

DNT-YT (Do-Not-Track YouTube) is a lightweight YouTube caching + offline browsing API.

Inspired by [**Piped**](https://github.com/TeamPiped/Piped?utm_source=gnh1201) and [**Invidious**](https://github.com/iv-org/invidious?utm_source=gnh1201). However, their goals and typical use-cases differ from what I need, so I created **DNT-YT**.

**DNT-YT prioritizes offline YouTube video exploration above everything else.** In fact, this is the only purpose of the source code in this repository.

<img width="723" height="661" alt="Screenshot" src="https://github.com/user-attachments/assets/f553b642-2a31-47f6-be98-17e33c642cbb" />

## What it does

* Accepts various YouTube URL formats (or a raw `video_id`)
* Automatically requests caching when missing
* Stores cached media (video/audio) and serves them as browser-playable URLs
* Serves an HTML watch page that:

  * polls cache status every 5 seconds when not ready
  * auto-plays video (muted) once ready
  * uses a **Mute/Unmute toggle** as the user gesture to enable audio
  * keeps A/V synchronized in the browser (no ffmpeg)

## Architecture

* **API**: FastAPI
* **Queue**: RQ
* **Storage/State**: Redis (jobs/status) + local media volume
* **Downloader**: yt-dlp (downloads audio/video separately; no ffmpeg required)

## Endpoints

### Watch (HTML)

These routes render the HTML player page.
If the cache is missing, the server should enqueue a caching job automatically, then the page waits/polls until the cache becomes ready.

#### 1) Root `/<video_id>`

```text
/wLp_c3M-nPA
```

#### 2) Path-based watch

```text
/watch/wLp_c3M-nPA
```

#### 3) Query `v=<video_id>` (YouTube-like)

```text
/watch?v=wLp_c3M-nPA
```

#### 4) Query `url=<full-youtube-url>`

```text
/watch?url=https://www.youtube.com/watch?v=wLp_c3M-nPA
```

### API (JSON)

#### Request caching / play intent

Queues a cache job if missing.

```text
GET /v1/yt/play?url=<youtube_url>
```

Example:

```bash
curl "http://localhost:58000/v1/yt/play?url=https://www.youtube.com/watch?v=wLp_c3M-nPA"
```

Typical response:

```json
{
  "ok": true,
  "ready": false,
  "video_id": "wLp_c3M-nPA",
  "job_id": "..."
}
```

#### Cache status

Returns whether cache is ready and (when ready) URLs for cached media.

```text
GET /v1/yt/status?video_id=<video_id>
```

Example:

```bash
curl "http://localhost:58000/v1/yt/status?video_id=wLp_c3M-nPA"
```

Typical response:

```json
{
  "ok": true,
  "ready": true,
  "video_id": "wLp_c3M-nPA",
  "video_url": "/media/wLp_c3M-nPA/video",
  "audio_url": "/media/wLp_c3M-nPA/audio",
}
```

### Media (cached files)

These URLs are browser-playable once caching completes.

```text
GET /media/<video_id>/video
GET /media/<video_id>/audio
GET /media/<video_id>/thumbnail  # thumbnail image
```

Example:

```text
/media/wLp_c3M-nPA/video
/media/wLp_c3M-nPA/audio
/media/wLp_c3M-nPA/thumbnail  # thumbnail image
```

## Content negotiation (HTML vs JSON)

DNT-YT can decide response format based on request headers. Typical behavior:

* If the client requests `text/html`, return the watch page
* If the client requests `application/json`, return JSON (status or play response)
* (Optional) `oEmbed` / OpenGraph can be added for social previews

## Playback model (no ffmpeg)

DNT-YT downloads **audio and video separately** using yt-dlp and serves them as separate files.

The watch page:

* starts video playback automatically (usually requires `muted` for autoplay)
* uses a **Mute/Unmute toggle** button as the explicit user action to enable audio reliably
* keeps audio aligned to video time (periodic drift correction and seek sync)

This avoids any server-side muxing/merging and therefore avoids ffmpeg.

## Dependencies

* **yt-dlp** (required)
* Redis
* FastAPI / Uvicorn
* RQ

## Quick start (Docker)

Typical:

```bash
docker compose up --build
```

Then open:

* Watch page: `http://localhost:58000/wLp_c3M-nPA`
* API: `http://localhost:58000/v1/yt/play?url=...`

## Goals / non-goals

**Goals**

* Offline-first YouTube exploration
* Simple caching API
* Browser-playable cached URLs
* Minimal server-side processing (no ffmpeg)

**Non-goals**

* Full UI like a Piped or Invidious
* Account features / subscriptions / comments
* Complex transcoding pipelines

## CDN Cache Rules
For scenarios where a CDN is used for **long-distance data transfer**, it is recommended to configure appropriate cache rules to reduce bandwidth usage and improve performance.

The following example shows a cache rule configuration based on [Cloudflare](https://www.cloudflare.com/?utm_source=gnh1201):

```text
(http.request.full_uri wildcard "https://domain.tld/media/*/video")
or
(http.request.full_uri wildcard "https://domain.tld/media/*/audio")
or
(http.request.full_uri wildcard "https://domain.tld/media/*/thumbnail")
```

## Use cases
* [Preventing YouTube Tracking Links on ActivityPub Servers](https://github.com/gnh1201/activitypub/blob/main/youtube.md?utm_source=gnh1201)

## My test videos
* https://dnt-yt.catswords.net/wLp_c3M-nPA (VHS Visual Doctor)

## Disclaimer
This software is licensed under the **GNU General Public License v3.0** and is provided **without any warranty**, to the extent permitted by applicable law. See the GPL v3.0 license for details.

The authors and contributors shall not be held liable for any damages arising from the use of this software. Any illegal or unauthorized use is solely the responsibility of the user, who must ensure compliance with all applicable laws and regulations.

## Join the community
I am always open. Collaboration, opportunities, and community activities are all welcome.

* ActivityPub [@catswords_oss@catswords.social](https://catswords.social/@catswords_oss?utm_source=gnh1201)
* XMPP [catswords@conference.omemo.id](xmpp:catswords@conference.omemo.id?join)
* [Join Catswords OSS on Microsoft Teams (teams.live.com)](https://teams.live.com/l/community/FEACHncAhq8ldnojAI?utm_source=gnh1201)
* [Join Catswords OSS #dnt-yt on Discord (discord.gg)](https://discord.gg/sZPCYDGWGM?utm_source=gnh1201)
