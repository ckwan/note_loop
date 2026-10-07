import logging
import time

from redis import Redis
from redis.exceptions import RedisError

from .errors import RateLimitedError

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def check_rate_limit(r: Redis, name: str, limit: int, window_seconds: int = 60) -> None:
    """Fixed window counter. Raises RateLimitedError when the limit is exceeded.

    Fails open: if Redis is down, the request is allowed. This limit protects LLM
    spend, which is not worth blocking a clinician over.
    """
    now = _now()
    bucket = int(now // window_seconds)
    key = f"ratelimit:{name}:{bucket}"
    try:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds * 2)
        count, _ = pipe.execute()
    except RedisError:
        logger.warning("Redis unavailable, skipping rate limit for %s", name)
        return

    if count > limit:
        retry_after = max(1, window_seconds - int(now) % window_seconds)
        raise RateLimitedError(retry_after)
