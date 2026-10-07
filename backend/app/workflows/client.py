import asyncio

from temporalio.client import Client

from app.config import get_settings

_client: Client | None = None
_lock = asyncio.Lock()


async def get_client() -> Client:
    """Connect once and reuse the client. Fails fast if Temporal is unreachable."""
    global _client
    async with _lock:
        if _client is None:
            settings = get_settings()
            _client = await asyncio.wait_for(
                Client.connect(
                    settings.temporal_address, namespace=settings.temporal_namespace
                ),
                timeout=3,
            )
    return _client
