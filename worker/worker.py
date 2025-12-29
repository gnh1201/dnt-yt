import os
import logging

from redis import Redis
from rq import Worker, Queue

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("worker")

def main():
    # Build Redis connection
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    conn = Redis.from_url(redis_url)

    # Create queue(s) with explicit connection
    q = Queue("yt", connection=conn)

    # Start worker with explicit connection
    w = Worker([q], connection=conn)
    logger.info("RQ worker starting (queues=%s, redis=%s)", ["yt"], redis_url)
    w.work(with_scheduler=False)

if __name__ == "__main__":
    main()
