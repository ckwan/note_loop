from functools import lru_cache

from redis import Redis

from .config import get_settings


@lru_cache
def _client() -> Redis:
    # Short timeouts so a Redis outage slows a request by about a second, not forever.
    return Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def get_redis_client() -> Redis:
    return _client()
