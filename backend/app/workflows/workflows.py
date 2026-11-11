import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from app.notify import messages
    from app.notify.types import Notification
    from app.workflows.activities import (
        check_claim_decision,
        mark_claim_follow_up,
        notify_claim_event,
        fetch_failed_claims,
        get_claim_counts,
        mark_claim_failed,
        notify_slack,
        prepare_claim,
        reset_claim_for_resubmit,
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

# Slack messages get a few quick tries. They are never worth holding a workflow up.
NOTIFY_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3)
NOTIFY_TIMEOUT = timedelta(seconds=10)


async def _notify(notification: Notification) -> None:
    """Send a Slack message. If it still fails after its retries, carry on."""
    try:
        await workflow.execute_activity(
            notify_slack,
            args=[notification, workflow.info().workflow_id],
            start_to_close_timeout=NOTIFY_TIMEOUT,
            retry_policy=NOTIFY_RETRY,
        )
    except ActivityError:
        workflow.logger.warning("Slack notification failed: %s", notification.title)


async def _notify_event(notification: Notification) -> None:
    """A routine claim event. The activity decides whether Slack gets it."""
    try:
        await workflow.execute_activity(
            notify_claim_event,
            args=[notification, workflow.info().workflow_id],
            start_to_close_timeout=NOTIFY_TIMEOUT,
            retry_policy=NOTIFY_RETRY,
        )
    except ActivityError:
        workflow.logger.warning("Slack notification failed: %s", notification.title)


# The batch workflow restarts itself with a fresh history after this many chunks,
# so a very large batch never grows one workflow's history without limit.
CHUNKS_PER_RUN = 20


CLAIM_TRACKING_PATCH = "claim-decision-tracking"
CLAIM_EVENTS_PATCH = "claim-event-notifications"


async def _track_decision(claim_id: int, note_id: int) -> None:
    """Poll the adjudication service until a final decision or the maximum wait.

    A tracking failure never fails the workflow. The claim is already submitted, so it
    moves to manual follow up instead.
    """
    try:
        check = await workflow.execute_activity(
            check_claim_decision,
            claim_id,
            task_queue=PAYER_TASK_QUEUE,
            start_to_close_timeout=TIMEOUT,
            retry_policy=RETRY,
        )
        waited = 0.0
        while check.state == "pending" and waited < check.max_wait_seconds:
            await workflow.sleep(check.poll_interval_seconds)
            waited += check.poll_interval_seconds
            check = await workflow.execute_activity(
                check_claim_decision,
                claim_id,
                task_queue=PAYER_TASK_QUEUE,
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
    except ActivityError:
        check = None

    if check is not None and check.state == "denied":
        await _notify(messages.claim_denied(note_id, claim_id))
    elif check is not None and check.state == "approved":
        if workflow.patched(CLAIM_EVENTS_PATCH):
            await _notify_event(messages.claim_approved(note_id, claim_id))
    elif check is not None and check.state == "not_applicable":
        return
    else:
        await workflow.execute_activity(
            mark_claim_follow_up,
            claim_id,
            start_to_close_timeout=TIMEOUT,
            retry_policy=RETRY,
        )
        await _notify(messages.claim_needs_follow_up(note_id, claim_id))


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
            reference = await workflow.execute_activity(
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
            await _notify(messages.claim_failed(note_id))
            raise

        if workflow.patched(CLAIM_EVENTS_PATCH):
            await _notify_event(messages.claim_submitted(note_id, claim_id))
        # Workflows already running when this was added never reach this step.
        if workflow.patched(CLAIM_TRACKING_PATCH):
            await _track_decision(claim_id, note_id)
        return reference


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
class ResubmitSingleClaimWorkflow:
    """A person asked to send one failed or denied claim again.

    Unlike the batch child, this waits for the decision, because the person is watching.
    """

    @workflow.run
    async def run(self, ref: ClaimRef) -> str:
        try:
            await workflow.execute_activity(
                reset_claim_for_resubmit,
                ref.claim_id,
                start_to_close_timeout=TIMEOUT,
                retry_policy=RETRY,
            )
            reference = await workflow.execute_activity(
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
            await _notify(messages.claim_failed(ref.note_id))
            raise

        await _notify_event(messages.claim_submitted(ref.note_id, ref.claim_id))
        await _track_decision(ref.claim_id, ref.note_id)
        return reference


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

            if params.after_id == 0 and chunks_this_run == 0:
                # Only the first run of the batch. A continue as new run starts
                # with a cursor, so this message is not repeated.
                await _notify(messages.batch_started(params.chunk_size))

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

        await _notify(
            messages.batch_finished(params.processed, params.succeeded, params.failed)
        )
        return BatchResult(
            processed=params.processed,
            succeeded=params.succeeded,
            failed=params.failed,
        )


@workflow.defn
class DailySummaryWorkflow:
    """Post claim counts to Slack. Started once a day by a Temporal Schedule."""

    @workflow.run
    async def run(self) -> None:
        counts = await workflow.execute_activity(
            get_claim_counts,
            start_to_close_timeout=TIMEOUT,
            retry_policy=RETRY,
        )
        await _notify(messages.daily_summary(counts))
