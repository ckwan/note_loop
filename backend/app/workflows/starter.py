from typing import Protocol

from temporalio.exceptions import WorkflowAlreadyStartedError

from app.config import get_settings
from app.workflows.client import get_client
from app.workflows.workflows import NoteSignedWorkflow


class WorkflowStarter(Protocol):
    async def start_note_signed(self, note_id: int) -> None: ...


class NoopStarter:
    """Used when TEMPORAL_ENABLED=false. The claim stays pending."""

    async def start_note_signed(self, note_id: int) -> None:
        return None


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
