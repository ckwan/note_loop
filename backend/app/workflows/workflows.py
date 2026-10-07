from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import mark_claim_failed, prepare_claim, submit_claim

RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
)
TIMEOUT = timedelta(seconds=30)


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
