import logging

from fastapi import APIRouter, Depends, Header
from fastapi.concurrency import run_in_threadpool
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .config import get_settings
from .db import get_db
from .errors import NotFoundError
from .idempotency import sign_once
from .llm import LLMProvider, build_provider
from .models import TherapySession
from .ratelimit import check_rate_limit
from .redis_client import get_redis_client
from .schemas import (
    AuditOut,
    ClaimOut,
    DraftRequest,
    NoteOut,
    NoteUpdate,
    SessionOut,
)
from .services import notes as svc
from .workflows.starter import NoopStarter, TemporalStarter, WorkflowStarter

logger = logging.getLogger(__name__)
router = APIRouter()


def get_llm_provider() -> LLMProvider:
    return build_provider(get_settings())


def get_workflow_starter() -> WorkflowStarter:
    return TemporalStarter() if get_settings().temporal_enabled else NoopStarter()


def limit_drafts(
    session_id: int,
    db: Session = Depends(get_db),
    r: Redis = Depends(get_redis_client),
) -> None:
    """Limit LLM calls per therapist. Unknown sessions fall through to the 404."""
    session = db.get(TherapySession, session_id)
    if session is not None:
        check_rate_limit(
            r,
            f"draft:{session.therapist_id}",
            get_settings().draft_rate_limit_per_minute,
        )


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(therapist_id: int | None = None, db: Session = Depends(get_db)):
    stmt = (
        select(TherapySession)
        .options(joinedload(TherapySession.patient), joinedload(TherapySession.note))
        .order_by(TherapySession.scheduled_at)
    )
    if therapist_id is not None:
        stmt = stmt.where(TherapySession.therapist_id == therapist_id)
    rows = db.execute(stmt).scalars().unique().all()
    return [svc.session_to_out(s) for s in rows]


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int, db: Session = Depends(get_db)):
    session = db.get(TherapySession, session_id)
    if session is None:
        raise NotFoundError(f"Session {session_id} not found")
    return svc.session_to_out(session)


@router.post(
    "/sessions/{session_id}/draft",
    response_model=NoteOut,
    status_code=201,
    dependencies=[Depends(limit_drafts)],
)
def create_draft(
    session_id: int,
    body: DraftRequest,
    db: Session = Depends(get_db),
    provider: LLMProvider = Depends(get_llm_provider),
):
    note = svc.generate_draft(db, session_id, body.raw_input, provider)
    return svc.note_to_out(note)


@router.get("/sessions/{session_id}/note", response_model=NoteOut)
def get_note(session_id: int, db: Session = Depends(get_db)):
    return svc.note_to_out(svc._get_note(db, session_id))


@router.put("/sessions/{session_id}/note", response_model=NoteOut)
def update_note(session_id: int, body: NoteUpdate, db: Session = Depends(get_db)):
    note = svc.update_final_note(db, session_id, body.final_note)
    return svc.note_to_out(note)


@router.post("/sessions/{session_id}/note/sign", response_model=NoteOut)
async def sign_note(
    session_id: int,
    idempotency_key: str | None = Header(default=None, max_length=128),
    db: Session = Depends(get_db),
    r: Redis = Depends(get_redis_client),
    starter: WorkflowStarter = Depends(get_workflow_starter),
):
    out, replayed = await run_in_threadpool(sign_once, db, r, session_id, idempotency_key)
    if not replayed:
        try:
            await starter.start_note_signed(out.id)
        except Exception:
            # The note is signed and its claim row is "pending". Do not fail the sign.
            logger.exception("Could not start the claim workflow for note %s", out.id)
    return out


@router.get("/sessions/{session_id}/claim", response_model=ClaimOut)
def get_claim(session_id: int, db: Session = Depends(get_db)):
    claim = svc.get_claim(db, session_id)
    return ClaimOut(
        id=claim.id,
        note_id=claim.note_id,
        status=claim.status,
        payer_reference=claim.payer_reference,
        submitted_at=claim.submitted_at,
    )


@router.get("/sessions/{session_id}/note/audit", response_model=list[AuditOut])
def get_audit(session_id: int, db: Session = Depends(get_db)):
    events = svc.list_audit(db, session_id)
    return [
        AuditOut(
            id=e.id,
            actor=e.actor,
            action=e.action,
            detail=e.detail,
            created_at=e.created_at,
        )
        for e in events
    ]
