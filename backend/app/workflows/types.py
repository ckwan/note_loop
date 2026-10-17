"""Plain data types shared by workflows and activities.

Kept in their own module so workflow code and activity code can import them
without pulling in database or config code.
"""

from dataclasses import dataclass

# All payer calls run on this queue, so one worker setting rate limits them all.
PAYER_TASK_QUEUE = "noteloop-payer"

# The batch workflow has one fixed ID, so only one batch can run at a time.
BATCH_WORKFLOW_ID = "resubmit-failed-claims"


@dataclass
class ClaimRef:
    claim_id: int
    note_id: int


@dataclass
class BatchParams:
    chunk_size: int = 25
    # Cursor. Claims are processed in id order, so a claim that fails again is
    # not picked up a second time in the same batch.
    after_id: int = 0
    # Running totals, carried across continue as new.
    processed: int = 0
    succeeded: int = 0
    failed: int = 0


@dataclass
class BatchResult:
    processed: int
    succeeded: int
    failed: int
