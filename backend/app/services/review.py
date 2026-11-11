"""Review before signing. Wraps the LangGraph review for the API.

A review never changes the saved note. It returns proposed text, and the client
saves what the therapist accepts through the normal edit endpoint.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import ConflictError, NotFoundError
from ..models import AuditEvent
from ..review import ReviewResult, resume_review, review_status, start_review
from ..schemas import (
    ReviewAnswerRequest,
    ReviewOut,
    SoapNote,
)
from .notes import _actor, _audit, _get_note


def _count_reviews(db: Session, note_id: int) -> int:
    return db.execute(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.note_id == note_id, AuditEvent.action == "review_started")
    ).scalar_one()


def _thread_id(note_id: int, number: int) -> str:
    # Each review of a note gets its own thread, so starting again never reuses
    # state from an earlier review.
    return f"note-{note_id}-review-{number}"


def _to_out(result: ReviewResult) -> ReviewOut:
    return ReviewOut(
        status=result.status,
        round=result.round,
        note=SoapNote(**result.note),
        findings=result.findings,
        fixes=result.fixes,
    )


def _summary(result: ReviewResult) -> list[dict]:
    # Sections and kinds only. The finding text can quote the note, so it stays out.
    return [
        {"section": f["section"], "kind": f["kind"], "source": f["source"]}
        for f in result.findings
    ]


def _editable_note(db: Session, session_id: int):
    note = _get_note(db, session_id)
    if note.status == "signed":
        raise ConflictError("A signed note cannot be reviewed")
    return note


def start(db: Session, graph, session_id: int, current: SoapNote) -> ReviewOut:
    note = _editable_note(db, session_id)
    number = _count_reviews(db, note.id) + 1
    result = start_review(graph, _thread_id(note.id, number), note.raw_input, current.model_dump())
    _audit(
        db,
        note,
        _actor(note.session),
        "review_started",
        {"status": result.status, "round": result.round, "findings": _summary(result)},
    )
    db.commit()
    return _to_out(result)


def answer(
    db: Session, graph, session_id: int, body: ReviewAnswerRequest
) -> ReviewOut:
    note = _editable_note(db, session_id)
    number = _count_reviews(db, note.id)
    if number == 0:
        raise ConflictError("No review has been started for this note")
    try:
        result = resume_review(
            graph, _thread_id(note.id, number), dict(body.decisions), dict(body.edits)
        )
    except ValueError as exc:
        raise ConflictError(str(exc)) from exc

    _audit(
        db,
        note,
        _actor(note.session),
        "review_answered",
        {
            "accepted": sorted(s for s, d in body.decisions.items() if d == "accept"),
            "dismissed": sorted(s for s, d in body.decisions.items() if d == "dismiss"),
            "edited": sorted(body.edits),
            "status": result.status,
            "round": result.round,
            "findings": _summary(result),
        },
    )
    db.commit()
    return _to_out(result)


def current(db: Session, graph, session_id: int) -> ReviewOut:
    note = _get_note(db, session_id)
    number = _count_reviews(db, note.id)
    result = review_status(graph, _thread_id(note.id, number)) if number else None
    if result is None:
        raise NotFoundError("No review has been started for this note")
    return _to_out(result)
