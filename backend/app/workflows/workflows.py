import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        fetch_failed_claims,
        mark_claim_failed,
        prepare_claim,
        reset_failed_claim,
        submit_claim,
    )
    from app.workflows.types import (
        PAYER_TASK_QUEUE,
        BatchParams,
        BatchResult,
        ClaimRef,
    )

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
TIMEOUT = timedelta(seconds=30)

# The batch workflow restarts itself with a fresh history after this many chunks,
# so a very large batch never grows one workflow's history without limit.
CHUNKS_PER_RUN = 20


@workflow.defn
class NoteSignedWorkflow:
    """Runs after a therapist signs a note: create the claim, then submit it.

    Workflow code must be deterministic. Anything with side effects (database,
    network, random, clocks) belongs in an activity.
    """

    @workflow.run
    async def run(self, note_id: int) -> str:
        try:
            claim_id = await workflow.execute_activity(
                prepare_claim,
                note_id,
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
            return await workflow.execute_activity(
                submit_claim,
                claim_id,
                task_queue=PAYER_TASK_QUEUE,
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
        except ActivityError:
            await workflow.execute_activity(
                mark_claim_failed,
                note_id,
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
            raise


@workflow.defn
class ResubmitClaimWorkflow:
    """Retry one failed claim. Started as a child of the batch workflow."""

    @workflow.run
    async def run(self, ref: ClaimRef) -> str:
        try:
            await workflow.execute_activity(
                reset_failed_claim,
                ref.claim_id,
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
            return await workflow.execute_activity(
                submit_claim,
                ref.claim_id,
                task_queue=PAYER_TASK_QUEUE,
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
        except ActivityError:
            await workflow.execute_activity(
                mark_claim_failed,
                ref.note_id,
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
            raise


@workflow.defn
class ResubmitFailedClaimsWorkflow:
    """Resubmit every failed claim, one chunk at a time.

    1. Fetch a page of failed claims after the cursor.
    2. Start one child workflow per claim and wait for the whole chunk.
    3. Advance the cursor and repeat until no failed claims remain.

    Each claim is its own child workflow, so one bad claim fails and retries on
    its own without stopping the rest. The pace toward the payer is limited by the
    payer worker's rate limit, not by this workflow.
    """

    @workflow.run
    async def run(self, params: BatchParams) -> BatchResult:
        chunks_this_run = 0

        while True:
            page = await workflow.execute_activity(
                fetch_failed_claims,
                args=[params.after_id, params.chunk_size],
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
            if not page:
                break

            outcomes = await asyncio.gather(
                *[
                    workflow.execute_child_workflow(
                        ResubmitClaimWorkflow.run,
                        ref,
                        id=f"resubmit-{ref.claim_id}",
                    )
                    for ref in page
                ],
                return_exceptions=True,
            )
            ok = sum(1 for o in outcomes if not isinstance(o, BaseException))

            params = BatchParams(
                chunk_size=params.chunk_size,
                after_id=page[-1].claim_id,
                processed=params.processed + len(page),
                succeeded=params.succeeded + ok,
                failed=params.failed + len(page) - ok,
            )

            chunks_this_run += 1
            if chunks_this_run >= CHUNKS_PER_RUN:
                workflow.continue_as_new(params)

        return BatchResult(
            processed=params.processed,
            succeeded=params.succeeded,
            failed=params.failed,
        )
