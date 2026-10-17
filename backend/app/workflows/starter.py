from typing import Protocol

from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import get_settings
from app.errors import ConflictError, UnavailableError
from app.workflows.client import get_client
from app.workflows.types import BATCH_WORKFLOW_ID, BatchParams
from app.workflows.workflows import NoteSignedWorkflow, ResubmitFailedClaimsWorkflow


class WorkflowStarter(Protocol):
    async def start_note_signed(self, note_id: int) -> None: ...

    async def start_resubmit_failed(self, chunk_size: int) -> str:
        """Start the batch workflow. Returns its workflow id."""
        ...


class NoopStarter:
    """Used when TEMPORAL_ENABLED=false. The claim stays pending."""

    async def start_note_signed(self, note_id: int) -> None:
        return None

    async def start_resubmit_failed(self, chunk_size: int) -> str:
        raise UnavailableError("Temporal is turned off (TEMPORAL_ENABLED=false)")


class TemporalStarter:
    async def start_note_signed(self, note_id: int) -> None:
        client = await get_client()
        try:
            # A fixed workflow id means one workflow per note, even if this runs twice.
            await client.start_workflow(
                NoteSignedWorkflow.run,
                note_id,
                id=f"note-signed-{note_id}",
                task_queue=get_settings().temporal_task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass

    async def start_resubmit_failed(self, chunk_size: int) -> str:
        try:
            client = await get_client()
        except Exception as exc:
            raise UnavailableError("Could not reach Temporal") from exc
        try:
            # One fixed id, so a second batch cannot start while one is running.
            await client.start_workflow(
                ResubmitFailedClaimsWorkflow.run,
                BatchParams(chunk_size=chunk_size),
                id=BATCH_WORKFLOW_ID,
                task_queue=get_settings().temporal_task_queue,
            )
        except WorkflowAlreadyStartedError as exc:
            raise ConflictError("A resubmit batch is already running") from exc
        return BATCH_WORKFLOW_ID
