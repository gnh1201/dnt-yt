import re
import subprocess
from typing import Optional, Tuple

_YT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

def extract_youtube_id(url_or_id: str) -> Optional[str]:
    """
    Extract 11-char YouTube video id from common URL forms or accept direct id.
    """
    if not url_or_id:
        return None

    s = url_or_id.strip()

    if _YT_ID_RE.match(s):
        return s

    m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", s)
    if m:
        return m.group(1)

    m = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", s)
    if m:
        return m.group(1)

    m = re.search(r"/shorts/([a-zA-Z0-9_-]{11})", s)
    if m:
        return m.group(1)

    m = re.search(r"/embed/([a-zA-Z0-9_-]{11})", s)
    if m:
        return m.group(1)

    return None

def ytdlp_print_id(url: str, timeout_seconds: int = 20) -> Optional[str]:
    """
    Resolve id via yt-dlp without downloading.
    """
    args = [
        "yt-dlp",
        "--js-runtimes", "deno",
        "--no-playlist",
        "--skip-download",
        "--print", "id",
        "--extractor-args", "youtube:player_client=android",
        "--force-ipv4",
        url,
    ]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_seconds)
    if proc.returncode != 0:
        return None

    line = (proc.stdout or "").strip().splitlines()[:1]
    if not line:
        return None

    vid = line[0].strip()
    return vid if _YT_ID_RE.match(vid) else None

def build_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"

def run_ytdlp_download(args: list[str], timeout_seconds: int) -> Tuple[int, str, str]:
    """
    Run yt-dlp and return (returncode, stdout, stderr).
    """
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_seconds)
    return proc.returncode, proc.stdout or "", proc.stderr or ""

