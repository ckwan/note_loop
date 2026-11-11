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
from app.notify.notifier import build_notifier
from app.notify.types import Notification
from app.payer.claims_api import build_claims_client, build_payload
from app.services import notes as notes_service
from app.workflows.types import ClaimRef, DecisionCheck

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
    if get_settings().payer_mode == "adjudication":
        return _submit_to_adjudication(claim_id, attempt)
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


def _submit_to_adjudication(claim_id: int, attempt: int) -> str:
    """POST the claim. external_ref is the note id, so a retry returns the same claim."""
    settings = get_settings()
    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        if claim is None:
            raise ApplicationError(f"Claim {claim_id} not found", non_retryable=True)
        if claim.status == "submitted" and claim.remote_claim_id:
            return claim.payer_reference or ""

        note = db.get(Note, claim.note_id)
        amount = float(claim.amount) if claim.amount is not None else settings.claim_default_amount
        # Only ids are read here. No note text, session notes, or patient name.
        payload = build_payload(
            settings.claims_org_id or "", claim.note_id, note.session.patient_id, amount
        )
        note_id = claim.note_id

    client = build_claims_client(settings)
    try:
        remote = client.create_claim(payload)
    finally:
        client.close()

    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        if claim.status != "submitted":
            claim.status = "submitted"
            claim.amount = amount
            claim.remote_claim_id = str(remote["id"])[:64]
            claim.remote_status = remote["status"]
            claim.payer_reference = claim.remote_claim_id[:40]
            claim.submitted_at = utcnow()
            claim.updated_at = claim.submitted_at
            _audit(
                db,
                note_id,
                "claim_submitted",
                {
                    "payer_reference": claim.payer_reference,
                    "remote_status": claim.remote_status,
                    "attempts": attempt,
                },
            )
        db.commit()
        return claim.payer_reference or ""


_FINAL = {"approved", "denied"}


@activity.defn
def check_claim_decision(claim_id: int) -> DecisionCheck:
    """One status check. Records each change of status, and the final decision.

    Simulated mode has nothing to track, so it answers not_applicable.
    """
    settings = get_settings()
    if settings.payer_mode != "adjudication":
        return DecisionCheck(state="not_applicable")

    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        if claim is None or not claim.remote_claim_id:
            raise ApplicationError(f"Claim {claim_id} has no remote claim", non_retryable=True)
        remote_id, note_id, previous = claim.remote_claim_id, claim.note_id, claim.remote_status

    client = build_claims_client(settings)
    try:
        remote_status = client.get_claim(remote_id)["status"]
    finally:
        client.close()

    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        if remote_status != previous:
            claim.remote_status = remote_status
            claim.updated_at = utcnow()
            _audit(
                db,
                note_id,
                "claim_remote_status",
                {"from": previous, "to": remote_status},
            )
        if remote_status in _FINAL and claim.status != remote_status:
            claim.status = remote_status
            claim.updated_at = utcnow()
            _audit(db, note_id, f"claim_{remote_status}", {"claim_id": claim_id})
        db.commit()

    return DecisionCheck(
        state=remote_status if remote_status in _FINAL else "pending",
        poll_interval_seconds=settings.claims_poll_interval_seconds,
        max_wait_seconds=settings.claims_poll_max_wait_seconds,
    )


@activity.defn
def mark_claim_follow_up(claim_id: int) -> None:
    """No final decision in time. A person needs to check. Safe to repeat."""
    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        if claim is not None and claim.status == "submitted":
            claim.status = "manual_follow_up"
            claim.updated_at = utcnow()
            _audit(db, claim.note_id, "claim_follow_up_needed", {"claim_id": claim_id})
        db.commit()


@activity.defn
def fetch_failed_claims(after_id: int, limit: int) -> list[ClaimRef]:
    """Return the next page of failed claims, ordered by id, after the cursor."""
    with SessionLocal() as db:
        rows = db.execute(
            select(Claim.id, Claim.note_id)
            .where(Claim.status == "failed", Claim.id > after_id)
            .order_by(Claim.id)
            .limit(limit)
        ).all()
        return [ClaimRef(claim_id=r.id, note_id=r.note_id) for r in rows]


@activity.defn
def reset_failed_claim(claim_id: int) -> int:
    """Move a failed claim back to created so it can be submitted again.

    Safe to repeat. A claim that is no longer failed is left as it is.
    """
    with SessionLocal() as db:
        claim = db.get(Claim, claim_id)
        if claim is None:
            raise ApplicationError(f"Claim {claim_id} not found", non_retryable=True)

        if claim.status == "failed":
            claim.status = "created"
            claim.updated_at = utcnow()
            _audit(db, claim.note_id, "claim_resubmit_requested", {"claim_id": claim.id})
        db.commit()
        return claim.id


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


@activity.defn
def notify_slack(notification: Notification, workflow_id: str | None = None) -> None:
    """Send one message. Raises if Slack fails, so Temporal retries it.

    The workflow decides what a final failure means. For notifications it means
    nothing: a claim or a batch never fails because Slack was down.
    """
    settings = get_settings()
    if workflow_id and not notification.link:
        notification.link = (
            f"{settings.temporal_ui_url}/namespaces/{settings.temporal_namespace}"
            f"/workflows/{workflow_id}"
        )
        notification.link_text = "Open in Temporal"
    build_notifier(settings).send(notification)


@activity.defn
def get_claim_counts() -> dict[str, int]:
    """Claims by status, for the daily summary."""
    with SessionLocal() as db:
        return notes_service.claim_summary(db).model_dump()
