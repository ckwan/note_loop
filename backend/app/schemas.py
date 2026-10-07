from datetime import datetime

from pydantic import BaseModel, Field

SECTIONS = ("subjective", "objective", "assessment", "plan")


class SoapNote(BaseModel):
    subjective: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    assessment: str = Field(min_length=1)
    plan: str = Field(min_length=1)


class DraftRequest(BaseModel):
    raw_input: str = Field(min_length=20, max_length=8000)


class NoteUpdate(BaseModel):
    final_note: SoapNote


class NoteOut(BaseModel):
    id: int
    session_id: int
    status: str
    ai_draft: SoapNote | None
    final_note: SoapNote | None
    changed_sections: list[str]
    signed_at: datetime | None


class SessionOut(BaseModel):
    id: int
    patient_name: str
    scheduled_at: datetime
    note_status: str | None


class ClaimOut(BaseModel):
    id: int
    note_id: int
    status: str
    payer_reference: str | None
    submitted_at: datetime | None


class AuditOut(BaseModel):
    id: int
    actor: str
    action: str
    detail: dict
    created_at: datetime
