import os
from redis import Redis

def get_redis() -> Redis:
    url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    # IMPORTANT: Must be raw bytes for RQ compatibility
    return Redis.from_url(url, decode_responses=False)

