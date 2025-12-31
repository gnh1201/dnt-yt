import os
import json
import time
import glob
import hashlib
import logging
from typing import Any, Dict, Optional

from rq import get_current_job

from app.redis_client import get_redis
from app.ytdlp_utils import build_watch_url, run_ytdlp_download

logger = logging.getLogger("jobs")

# Root directory where media files are stored
MEDIA_ROOT = os.getenv("MEDIA_ROOT", "/data")

# TTL for informational cache (not media files)
INFO_TTL_SECONDS = int(os.getenv("INFO_TTL_SECONDS", "21600"))  # 6 hours


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _b(s: str) -> bytes:
    """Encode string to UTF-8 bytes."""
    return s.encode("utf-8")


def _hash(s: str) -> str:
    """Generate a stable hash for cache keys."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _pick_newest_nonempty(paths: list[str]) -> Optional[str]:
    """
    Pick the most recently modified file that is non-empty.
    This is important because yt-dlp may leave partial/empty files on failure.
    """
    candidates = [p for p in paths if os.path.isfile(p) and os.path.getsize(p) > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------

def k_media(video_id: str) -> str:
    """Redis key for cached media metadata."""
    return f"yt:media:{video_id}"


def k_lock(video_id: str) -> str:
    """Redis key for enqueue lock."""
    return f"yt:lock:{video_id}"


# ---------------------------------------------------------------------------
# Redis access helpers
# ---------------------------------------------------------------------------

def store_media(video_id: str, payload: Dict[str, Any]) -> None:
    """Store media metadata in Redis."""
    r = get_redis()
    r.set(_b(k_media(video_id)), json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def get_media(video_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve media metadata from Redis."""
    r = get_redis()
    raw = r.get(_b(k_media(video_id)))
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def release_lock(video_id: str) -> None:
    """Release enqueue lock for a video."""
    r = get_redis()
    r.delete(_b(k_lock(video_id)))


# ---------------------------------------------------------------------------
# Main job: download audio/video separately without ffmpeg
# ---------------------------------------------------------------------------

def download_av_job(video_id: str) -> Dict[str, Any]:
    """
    Download video-only and audio-only streams separately using yt-dlp.

    Design goals:
    - No ffmpeg dependency
    - Prefer browser-friendly containers (mp4/m4a)
    - Fallback to HLS (m3u8) when direct MP4/M4A is unavailable
    - Actual media segments are downloaded (not just m3u8 files)
    """

    job = get_current_job()
    started_at = time.time()

    os.makedirs(MEDIA_ROOT, exist_ok=True)

    watch_url = build_watch_url(video_id)

    # Output templates (extension decided by yt-dlp)
    video_tpl = os.path.join(MEDIA_ROOT, f"{video_id}.video.%(ext)s")
    audio_tpl = os.path.join(MEDIA_ROOT, f"{video_id}.audio.%(ext)s")

    # IMPORTANT:
    # - We intentionally do NOT force the android client.
    # - Forcing android requires a PO Token and breaks many formats otherwise.
    common = [
        "yt-dlp",
        "--no-playlist",
        "--force-ipv4",
        "--newline",
        "--no-continue",
        "--no-part",

        # Use yt-dlp's native HLS downloader (no ffmpeg)
        "--hls-prefer-native",

        "--retries", "5",
        "--fragment-retries", "5",
        "--retry-sleep", "1:3",
    ]

    # Video-only format selection strategy:
    # 1) MP4 with AVC (best browser compatibility)
    # 2) Any MP4 video-only
    # 3) HLS video-only
    # 4) Absolute fallback: any bestvideo
    video_format = (
        "bestvideo[ext=mp4][vcodec^=avc1]/"
        "bestvideo[ext=mp4]/"
        "bestvideo[protocol^=m3u8]/"
        "bestvideo"
    )

    video_args = common + [
        "-f", video_format,
        "-o", video_tpl,
        "--write-subs",
        "--write-auto-subs",
        "--sub-format", "vtt",
        "--output-na-placeholder", "\"\"",
        watch_url,
    ]

    # Audio-only format selection strategy:
    # 1) M4A (AAC in MP4 container)
    # 2) HLS audio-only
    # 3) Absolute fallback: any bestaudio
    audio_format = (
        "bestaudio[ext=m4a]/"
        "bestaudio[protocol^=m3u8]/"
        "bestaudio"
    )

    audio_args = common + [
        "-f", audio_format,
        "-o", audio_tpl,
        watch_url,
    ]

    try:
        # -------------------------------------------------------------------
        # Download video-only
        # -------------------------------------------------------------------
        rc_v, out_v, err_v = run_ytdlp_download(video_args, timeout_seconds=1800)
        if err_v:
            logger.warning("yt-dlp video stderr:\n%s", err_v.strip())
        if rc_v != 0:
            raise RuntimeError(
                f"yt-dlp video download failed (rc={rc_v}): {(err_v or '').strip()}"
            )

        # -------------------------------------------------------------------
        # Download audio-only
        # -------------------------------------------------------------------
        rc_a, out_a, err_a = run_ytdlp_download(audio_args, timeout_seconds=1800)
        if err_a:
            logger.warning("yt-dlp audio stderr:\n%s", err_a.strip())
        if rc_a != 0:
            raise RuntimeError(
                f"yt-dlp audio download failed (rc={rc_a}): {(err_a or '').strip()}"
            )

        # -------------------------------------------------------------------
        # Resolve actual output files
        # -------------------------------------------------------------------
        v_paths = glob.glob(os.path.join(MEDIA_ROOT, f"{video_id}.video.*"))
        a_paths = glob.glob(os.path.join(MEDIA_ROOT, f"{video_id}.audio.*"))

        video_path = _pick_newest_nonempty(v_paths)
        audio_path = _pick_newest_nonempty(a_paths)

        if not video_path:
            raise RuntimeError("Video download finished but output file is missing or empty")
        if not audio_path:
            raise RuntimeError("Audio download finished but output file is missing or empty")

        payload = {
            "video_id": video_id,
            "watch_url": watch_url,
            "video_path": video_path,
            "audio_path": audio_path,
            "updated_at": int(time.time()),
        }

        store_media(video_id, payload)

        elapsed_ms = int((time.time() - started_at) * 1000)

        return {
            "ok": True,
            "video_id": video_id,
            "job_id": job.id if job else None,
            "elapsed_ms": elapsed_ms,
            "video_path": video_path,
            "audio_path": audio_path,
        }

    finally:
        # IMPORTANT:
        # Always release the enqueue lock, even if the job fails.
        release_lock(video_id)

