from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, Numeric, ForeignKey, String, Text, event, inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Therapist(Base):
    __tablename__ = "therapists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True)


class Patient(Base):
    """Synthetic patients only. Never store real PHI in this project."""

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    therapist_id: Mapped[int] = mapped_column(ForeignKey("therapists.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))


class TherapySession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    therapist_id: Mapped[int] = mapped_column(ForeignKey("therapists.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Used to price the claim.
    duration_minutes: Mapped[int] = mapped_column(Integer, default=50, server_default="50")

    patient: Mapped[Patient] = relationship()
    note: Mapped["Note | None"] = relationship(back_populates="session", uselist=False)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), unique=True)
    raw_input: Mapped[str] = mapped_column(Text)
    # Written once when the draft is generated. Never changed afterwards.
    ai_draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The therapist's working copy. This is what gets signed.
    final_note: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft or signed
    signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    session: Mapped[TherapySession] = relationship(back_populates="note")


class AuditEvent(Base):
    """Append only. Rows are inserted and never updated or deleted."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id"), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Claim(Base):
    """A stub claim created when a note is signed. Moved along by the Temporal workflow."""

    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id"), unique=True)
    # pending (queued) -> created -> submitted, or failed.
    # Adjudication mode adds approved, denied, and manual_follow_up (the decision did
    # not arrive in time, so a person checks it).
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payer_reference: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Adjudication mode only.
    amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    remote_claim_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remote_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 0 for the first submission. A denied claim that is sent again gets a new external
    # reference (note id plus this number), because the API returns a repeated one unchanged.
    submission_attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


@event.listens_for(Note, "before_update")
def _ai_draft_is_immutable(mapper, connection, target: Note) -> None:
    history = inspect(target).attrs.ai_draft.history
    if history.has_changes() and history.deleted and history.deleted[0] is not None:
        raise ValueError("ai_draft is immutable once it has been written")
