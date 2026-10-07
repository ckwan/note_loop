"""Temporal activities. These do the side effects: database writes and the payer call.

Activities may run more than once (retries, worker restarts), so each one is
idempotent. Running an activity twice leaves the same end state as running it once.
"""

from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEvent, Claim, Note, utcnow

ACTOR = "system:temporal"


def _audit(db, note_id: int, action: str, detail: dict | None = None) -> None:
    db.add(AuditEvent(note_id=note_id, actor=ACTOR, action=action, detail=detail or {}))


@activity.defn
def prepare_claim(note_id: int) -> int:
    """Move the claim from pending to created. Returns the claim id."""
    with SessionLocal() as db:
        note = db.get(Note, note_id)
        if note is None or note.status != "signed":
            # Retrying cannot fix this, so fail the activity permanently.
            raise ApplicationError(
                f"Note {note_id} is not signed", non_retryable=True
            )

        claim = db.execute(
            select(Claim).where(Claim.note_id == note_id)
        ).scalar_one_or_none()
        if claim is None:
            claim = Claim(note_id=note_id)
            db.add(claim)
            db.flush()

        if claim.status == "pending":
            claim.status = "created"
            claim.updated_at = utcnow()
            _audit(db, note_id, "claim_created", {"claim_id": claim.id})
        db.commit()
        return claim.id


@activity.defn
def submit_claim(claim_id: int) -> str:
    """Send the claim to the payer (simulated). Returns the payer reference."""
    attempt = activity.info().attempt
    if attempt <= get_settings().simulate_payer_failures:
        raise RuntimeError(f"Payer gateway timeout (simulated, attempt {attempt})")

    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        if claim is None:
            raise ApplicationError(f"Claim {claim_id} not found", non_retryable=True)

        if claim.status != "submitted":
            claim.status = "submitted"
            claim.payer_reference = f"SIM-{claim.id:06d}"
            claim.submitted_at = utcnow()
            claim.updated_at = claim.submitted_at
            _audit(
                db,
                claim.note_id,
                "claim_submitted",
                {"payer_reference": claim.payer_reference, "attempts": attempt},
            )
        db.commit()
        return claim.payer_reference or ""


@activity.defn
def mark_claim_failed(note_id: int) -> None:
    """Record that every retry was used up. A person needs to look at this claim."""
    with SessionLocal() as db:
        claim = db.execute(
            select(Claim).where(Claim.note_id == note_id)
        ).scalar_one_or_none()
        if claim is not None and claim.status != "failed":
            claim.status = "failed"
            claim.updated_at = utcnow()
            _audit(db, note_id, "claim_failed")
        db.commit()
