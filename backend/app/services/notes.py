"""Business logic for notes. Routes call these functions and nothing else."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ConflictError, LLMOutputError, NotFoundError
from ..llm import LLMProvider, parse_soap_json
from ..models import AuditEvent, Claim, Note, TherapySession, utcnow
from ..schemas import SECTIONS, NoteOut, SessionOut, SoapNote

MAX_ATTEMPTS = 2  # one try plus one retry on invalid model output


def _get_session(db: Session, session_id: int) -> TherapySession:
    session = db.get(TherapySession, session_id)
    if session is None:
        raise NotFoundError(f"Session {session_id} not found")
    return session


def _get_note(db: Session, session_id: int) -> Note:
    session = _get_session(db, session_id)
    if session.note is None:
        raise NotFoundError(f"Session {session_id} has no note yet")
    return session.note


def _actor(session: TherapySession) -> str:
    # Real authentication arrives later. For now the session owner is the actor.
    return f"therapist:{session.therapist_id}"


def _audit(db: Session, note: Note, actor: str, action: str, detail: dict | None = None):
    db.add(AuditEvent(note_id=note.id, actor=actor, action=action, detail=detail or {}))


def changed_sections(note: Note) -> list[str]:
    if not note.ai_draft or not note.final_note:
        return []
    return [s for s in SECTIONS if note.ai_draft[s] != note.final_note[s]]


def note_to_out(note: Note) -> NoteOut:
    return NoteOut(
        id=note.id,
        session_id=note.session_id,
        status=note.status,
        ai_draft=SoapNote(**note.ai_draft) if note.ai_draft else None,
        final_note=SoapNote(**note.final_note) if note.final_note else None,
        changed_sections=changed_sections(note),
        signed_at=note.signed_at,
    )


def session_to_out(session: TherapySession) -> SessionOut:
    return SessionOut(
        id=session.id,
        patient_name=session.patient.display_name,
        scheduled_at=session.scheduled_at,
        note_status=session.note.status if session.note else None,
    )


def generate_draft(
    db: Session, session_id: int, raw_input: str, provider: LLMProvider
) -> Note:
    session = _get_session(db, session_id)
    if session.note is not None and session.note.ai_draft is not None:
        raise ConflictError("A draft already exists for this session")

    soap: SoapNote | None = None
    attempts = 0
    for attempts in range(1, MAX_ATTEMPTS + 1):
        try:
            text = provider.draft_soap(raw_input)
        except Exception as exc:
            raise LLMOutputError("The LLM provider call failed") from exc
        try:
            soap = parse_soap_json(text)
            break
        except ValueError:
            continue
    if soap is None:
        raise LLMOutputError("The model did not return a valid SOAP note")

    note = Note(
        session_id=session_id,
        raw_input=raw_input,
        ai_draft=soap.model_dump(),
        final_note=soap.model_dump(),
        status="draft",
    )
    db.add(note)
    db.flush()
    _audit(db, note, _actor(session), "draft_generated", {"attempts": attempts})
    db.commit()
    return note


def update_final_note(db: Session, session_id: int, final_note: SoapNote) -> Note:
    note = _get_note(db, session_id)
    if note.status == "signed":
        raise ConflictError("A signed note cannot be edited")

    new_value = final_note.model_dump()
    edited = [s for s in SECTIONS if new_value[s] != note.final_note[s]]
    if edited:
        note.final_note = new_value
        note.updated_at = utcnow()
        _audit(db, note, _actor(note.session), "edited", {"sections": edited})
        db.commit()
    return note


def sign_note(db: Session, session_id: int) -> Note:
    note = _get_note(db, session_id)
    if note.status == "signed":
        raise ConflictError("This note is already signed")

    note.status = "signed"
    note.signed_at = utcnow()
    note.updated_at = note.signed_at
    _audit(
        db,
        note,
        _actor(note.session),
        "signed",
        {"edited_sections": changed_sections(note)},
    )
    # Created in the same transaction as the signature. The Temporal workflow picks
    # it up next. If the workflow cannot start, the claim stays "pending" and is
    # visible, instead of the signature silently losing its follow up.
    db.add(Claim(note_id=note.id))
    db.commit()
    return note


def get_claim(db: Session, session_id: int) -> Claim:
    note = _get_note(db, session_id)
    claim = db.execute(select(Claim).where(Claim.note_id == note.id)).scalar_one_or_none()
    if claim is None:
        raise NotFoundError(f"Session {session_id} has no claim yet")
    return claim


def list_audit(db: Session, session_id: int) -> list[AuditEvent]:
    note = _get_note(db, session_id)
    stmt = select(AuditEvent).where(AuditEvent.note_id == note.id).order_by(AuditEvent.id)
    return list(db.execute(stmt).scalars())
