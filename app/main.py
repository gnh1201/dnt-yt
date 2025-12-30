import re
import os
import mimetypes
import logging
from typing import Optional, Dict, Any

from fastapi import FastAPI, Path, Query, Request, HTTPException
from fastapi.responses import Response, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from rq import Queue

import httpx

from app.logging_config import setup_logging
from app.redis_client import get_redis
from app.ytdlp_utils import extract_youtube_id, ytdlp_print_id
from app.jobs import get_media, download_av_job

setup_logging()
logger = logging.getLogger("api")

# YouTube video ID validation
VIDEO_ID_REGEX = "^[A-Za-z0-9_-]{10,14}$"

# Candidate thumbnail filenames to try.
# Earlier items are expected to have higher resolution/quality.
POSSIBLE_THUMBNAILS = [
    "maxresdefault.jpg",
    "hq720.jpg",
    "sddefault.jpg",
    "hqdefault.jpg",
    "mqdefault.jpg",
    "default.jpg",
    "0.jpg", "1.jpg", "2.jpg", "3.jpg",
]

# Strong cache hints for browsers and CDNs
CACHE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable"
}

# Media storage root (shared with video/audio cache)
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "media")
os.makedirs(MEDIA_ROOT, exist_ok=True)

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:58000")

app = FastAPI(title="YT Cache API (split A/V, no ffmpeg)")

# Jinja2 template directory (HTML is separated from Python code)
templates = Jinja2Templates(directory="app/templates")

# Optional: mount static directory if you later split CSS/JS into separate files
# app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _accepts_html(req: Request) -> bool:
    """Return True if the client prefers HTML response."""
    a = (req.headers.get("accept") or "").lower()
    return "text/html" in a


def _accepts_oembed(req: Request) -> bool:
    """Return True if the client prefers an oEmbed JSON response."""
    a = (req.headers.get("accept") or "").lower()
    return "application/json+oembed" in a or ("application/json" in a and "oembed" in a)


def _queue() -> Queue:
    """Return the RQ queue for YouTube caching jobs."""
    return Queue("yt", connection=get_redis())


def resolve_video_id(
    video_id: Optional[str],
    url: Optional[str],
    request_path_video_id: Optional[str],
) -> Optional[str]:
    """
    Resolve YouTube 11-char video id from:
      - /watch/<video_id> (path)
      - ?v=<video_id> or ?video_id=<video_id>
      - ?url=<full URL containing video id>
      - direct full URL if passed as 'url'
    """
    if request_path_video_id:
        vid = extract_youtube_id(request_path_video_id)
        if vid:
            return vid

    if video_id:
        vid = extract_youtube_id(video_id)
        if vid:
            return vid

    if url:
        # Fast path: extract from URL string using regex
        vid = extract_youtube_id(url)
        if vid:
            return vid

        # Slow path: ask yt-dlp to print the id without downloading
        return ytdlp_print_id(url)

    return None


def ensure_cache_request(video_id: str) -> str:
    """
    Enqueue caching job if media is not ready.

    IMPORTANT:
    - Uses a Redis lock to deduplicate repeated enqueue calls during HTML polling.
    - Releases lock is handled by the job's finally block (jobs.py).
    """
    media = get_media(video_id)
    if media and media.get("video_path") and media.get("audio_path"):
        return ""

    r = get_redis()

    lock_key = f"yt:lock:{video_id}".encode("utf-8")
    got_lock = r.set(lock_key, b"1", nx=True, ex=600)  # 10 minutes lock

    if not got_lock:
        # A job is already queued/running.
        return ""

    q = _queue()
    job = q.enqueue(download_av_job, video_id, job_timeout=3600)

    # Store last job id for debugging/status
    r.set(f"yt:last_job:{video_id}".encode("utf-8"), job.id.encode("utf-8"), ex=3600)

    return job.id


def status_payload(video_id: str) -> Dict[str, Any]:
    """
    Build a stable JSON payload for status checking.
    Always schedules caching when not ready.
    """
    media = get_media(video_id)
    if media and media.get("video_path") and media.get("audio_path"):
        return {
            "ok": True,
            "ready": True,
            "video_id": video_id,
            "video_url": f"{PUBLIC_BASE_URL}/media/{video_id}/video",
            "audio_url": f"{PUBLIC_BASE_URL}/media/{video_id}/audio",
            "watch_url": f"{PUBLIC_BASE_URL}/watch/{video_id}",
            "job_id": None,
        }

    job_id = ensure_cache_request(video_id)
    return {
        "ok": True,
        "ready": False,
        "video_id": video_id,
        "video_url": None,
        "audio_url": None,
        "watch_url": f"{PUBLIC_BASE_URL}/watch/{video_id}",
        "job_id": job_id or None,
    }


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("app/static/favicon.ico")


@app.get("/v1/yt/play")
async def play(
    request: Request,
    v: Optional[str] = None,
    video_id: Optional[str] = None,
    url: Optional[str] = None,
    format: Optional[str] = None,
):
    """
    Content negotiation entry point.

    Supported inputs:
      - /v1/yt/play?v=<video_id>
      - /v1/yt/play?video_id=<video_id>
      - /v1/yt/play?url=<full_youtube_url>

    Response formats:
      - HTML: player page (polls every 5 seconds until ready)
      - JSON: status payload
      - oEmbed: oEmbed JSON document
    """
    vid = resolve_video_id(video_id=video_id or v, url=url, request_path_video_id=None)
    if not vid:
        raise HTTPException(status_code=400, detail="Unable to resolve video_id")

    fmt = (format or "").lower().strip()
    if fmt == "html":
        return await watch_page(request, vid)
    if fmt == "oembed":
        return oembed(request, vid)
    if fmt == "json":
        return JSONResponse(status_payload(vid))

    if _accepts_html(request):
        return await watch_page(request, vid)
    if _accepts_oembed(request):
        return oembed(request, vid)

    return JSONResponse(status_payload(vid))


@app.get("/v1/yt/status")
def status(
    request: Request,
    video_id: Optional[str] = None,
    v: Optional[str] = None,
    url: Optional[str] = None,
):
    """
    JSON status endpoint (always auto-enqueues caching if missing).

    Supported inputs:
      - /v1/yt/status?video_id=<video_id>
      - /v1/yt/status?v=<video_id>
      - /v1/yt/status?url=<full_youtube_url>
    """
    vid = resolve_video_id(video_id=video_id or v, url=url, request_path_video_id=None)
    if not vid:
        raise HTTPException(status_code=400, detail="Unable to resolve video_id")

    return JSONResponse(status_payload(vid))


@app.get("/watch")
async def watch_query(
    request: Request,
    v: Optional[str] = None,
    video_id: Optional[str] = None,
    url: Optional[str] = None,
):
    """
    Watch page entrypoint for query-based formats:
      - /watch?v=<video_id>
      - /watch?video_id=<video_id>
      - /watch?url=<full_youtube_url_with_id>
    """
    vid = resolve_video_id(video_id=video_id or v, url=url, request_path_video_id=None)
    if not vid:
        raise HTTPException(status_code=400, detail="Unable to resolve video_id")
    return await watch_page(request, vid)


@app.get("/watch/{path_video_id}")
async def watch_page(request: Request, path_video_id: str):
    """
    HTML watch page.
    If cache is missing, the page polls every 5 seconds until ready, then plays.
    """
    vid = resolve_video_id(
        video_id=None,
        url=request.query_params.get("url"),
        request_path_video_id=path_video_id,
    )
    if not vid:
        raise HTTPException(status_code=400, detail="Unable to resolve video_id")

    # Always ensure a cache request is scheduled if missing.
    ensure_cache_request(vid)

    # Render external template
    return templates.TemplateResponse(
        "watch.html",
        {
            "request": request,  # REQUIRED for Jinja2Templates
            "video_id": vid,
            "public_base_url": PUBLIC_BASE_URL,
        },
    )


@app.get("/media/{video_id}/video")
def media_video(video_id: str):
    """
    Serve cached video-only file.
    If missing, auto-enqueue caching and return 404.
    """
    vid = extract_youtube_id(video_id)
    if not vid:
        raise HTTPException(status_code=400, detail="Invalid video_id")

    media = get_media(vid)
    if not media or not media.get("video_path"):
        ensure_cache_request(vid)
        raise HTTPException(status_code=404, detail="Video not cached yet")

    p = media["video_path"]
    mime, _ = mimetypes.guess_type(p)
    return FileResponse(p, media_type=(mime or "application/octet-stream"), filename=os.path.basename(p), headers=CACHE_HEADERS)


@app.get("/media/{video_id}/audio")
def media_audio(video_id: str):
    """
    Serve cached audio-only file.
    If missing, auto-enqueue caching and return 404.
    """
    vid = extract_youtube_id(video_id)
    if not vid:
        raise HTTPException(status_code=400, detail="Invalid video_id")

    media = get_media(vid)
    if not media or not media.get("audio_path"):
        ensure_cache_request(vid)
        raise HTTPException(status_code=404, detail="Audio not cached yet")

    p = media["audio_path"]
    mime, _ = mimetypes.guess_type(p)
    return FileResponse(p, media_type=(mime or "application/octet-stream"), filename=os.path.basename(p), headers=CACHE_HEADERS)


@app.get("/media/{video_id}/thumbnail", include_in_schema=False)
async def thumbnail(
    # Validate video_id at the routing level using Path + regex
    video_id: str = Path(..., pattern=VIDEO_ID_REGEX),
):
    # Local thumbnail cache path (single best thumbnail per video)
    thumb_path = os.path.join(MEDIA_ROOT, f"{video_id}.thumb.jpg")

    # 1) Local cache hit: serve immediately
    if os.path.exists(thumb_path):
        with open(thumb_path, "rb") as f:
            return Response(
                f.read(),
                media_type="image/jpeg",
                headers=CACHE_HEADERS,
            )

    # Base URL for YouTube thumbnail assets
    base_url = f"https://i.ytimg.com/vi/{video_id}"

    # 2) Try possible thumbnails in order and cache the first successful one
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for name in POSSIBLE_THUMBNAILS:
            url = f"{base_url}/{name}"
            try:
                r = await client.get(url)
                if r.status_code != 200:
                    continue

                # Save to local cache
                with open(thumb_path, "wb") as f:
                    f.write(r.content)

                return Response(
                    r.content,
                    media_type="image/jpeg",
                    headers=CACHE_HEADERS,
                )
            except httpx.HTTPError as e:
                # Not a fatal error: move on to the next thumbnail candidate
                logger.info(
                    "Thumbnail not available, trying next candidate: %s (%s)",
                    url,
                    e,
                )
                continue

    # No thumbnail was found
    raise HTTPException(status_code=404)


@app.get("/oembed")
def oembed(
    request: Request,
    video_id: Optional[str] = None,
    url: Optional[str] = None,
    v: Optional[str] = None,
):
    """
    oEmbed JSON response.
    If cache is missing, auto-enqueue and still return a valid oEmbed document pointing to /watch.
    """
    vid = resolve_video_id(video_id=video_id or v, url=url, request_path_video_id=None)
    if not vid:
        raise HTTPException(status_code=400, detail="Unable to resolve video_id")

    ensure_cache_request(vid)

    watch = f"{PUBLIC_BASE_URL}/watch/{vid}"
    return JSONResponse(
        {
            "version": "1.0",
            "type": "video",
            "provider_name": "YT Cache API",
            "provider_url": PUBLIC_BASE_URL,
            "title": f"YT Cache {vid}",
            "author_name": "YT Cache API",
            "html": f'<iframe src="{watch}" width="560" height="315" frameborder="0" allowfullscreen></iframe>',
            "width": 560,
            "height": 315,
        }
    )

@app.get("/{video_id}", include_in_schema=False)
async def watch_by_root_video_id(request: Request, video_id: str = Path(..., pattern=VIDEO_ID_REGEX)):
    # Reuse existing watch page logic
    return await watch_page(request, video_id)
