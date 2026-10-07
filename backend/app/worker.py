"""Temporal worker. Run with: python -m app.worker"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import get_settings
from app.workflows.activities import mark_claim_failed, prepare_claim, submit_claim
from app.workflows.workflows import NoteSignedWorkflow

logger = logging.getLogger("noteloop.worker")


async def connect_with_retry(address: str, namespace: str, attempts: int = 30) -> Client:
    for attempt in range(1, attempts + 1):
        try:
            return await Client.connect(address, namespace=namespace)
        except Exception as exc:
            logger.warning("Temporal not ready (%s/%s): %s", attempt, attempts, exc)
            await asyncio.sleep(2)
    raise RuntimeError(f"Could not reach Temporal at {address}")


async def main() -> None:
    settings = get_settings()
    client = await connect_with_retry(settings.temporal_address, settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[NoteSignedWorkflow],
        activities=[prepare_claim, submit_claim, mark_claim_failed],
        # Activities are plain sync functions that use the sync SQLAlchemy session.
        activity_executor=ThreadPoolExecutor(max_workers=5),
    )
    logger.info("Worker listening on task queue %s", settings.temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
