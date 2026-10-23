"""Temporal worker. Run with: python -m app.worker"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import get_settings
from app.workflows.activities import (
    fetch_failed_claims,
    get_claim_counts,
    mark_claim_failed,
    notify_slack,
    prepare_claim,
    reset_failed_claim,
    submit_claim,
)
from app.workflows.types import PAYER_TASK_QUEUE
from app.workflows.workflows import (
    DailySummaryWorkflow,
    NoteSignedWorkflow,
    ResubmitClaimWorkflow,
    ResubmitFailedClaimsWorkflow,
)

logger = logging.getLogger("noteloop.worker")


def build_workers(
    client: Client,
    task_queue: str,
    payer_rate_per_second: float,
    executor: ThreadPoolExecutor,
) -> list[Worker]:
    """Two workers: one for workflows and internal work, one for payer calls.

    Every call to the payer goes through the second worker. Its queue wide rate
    limit caps how fast claims reach the payer, however many claims are waiting
    and however many workers are running.
    """
    main = Worker(
        client,
        task_queue=task_queue,
        workflows=[
            NoteSignedWorkflow,
            ResubmitClaimWorkflow,
            ResubmitFailedClaimsWorkflow,
            DailySummaryWorkflow,
        ],
        activities=[
            prepare_claim,
            mark_claim_failed,
            reset_failed_claim,
            fetch_failed_claims,
            notify_slack,
            get_claim_counts,
        ],
        # Activities are plain sync functions that use the sync SQLAlchemy session.
        activity_executor=executor,
    )
    payer = Worker(
        client,
        task_queue=PAYER_TASK_QUEUE,
        activities=[submit_claim],
        activity_executor=executor,
        # None means no limit.
        max_task_queue_activities_per_second=payer_rate_per_second or None,
    )
    return [main, payer]


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
    workers = build_workers(
        client,
        settings.temporal_task_queue,
        settings.payer_max_per_second,
        ThreadPoolExecutor(max_workers=10),
    )
    logger.info(
        "Workers listening on %s and %s (payer limit %s per second)",
        settings.temporal_task_queue,
        PAYER_TASK_QUEUE,
        settings.payer_max_per_second or "none",
    )
    await asyncio.gather(*(w.run() for w in workers))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
