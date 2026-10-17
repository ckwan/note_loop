from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SECTIONS = ("subjective", "objective", "assessment", "plan")
Section = Literal["subjective", "objective", "assessment", "plan"]


class SoapNote(BaseModel):
    subjective: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    assessment: str = Field(min_length=1)
    plan: str = Field(min_length=1)


class DraftRequest(BaseModel):
    raw_input: str = Field(min_length=20, max_length=8000)


class NoteUpdate(BaseModel):
    final_note: SoapNote
    # Sections whose text came from an accepted AI wording suggestion. Recorded in
    # the audit trail so it shows which edits were AI assisted.
    ai_assisted_sections: list[Section] = Field(default_factory=list)


class SuggestRequest(BaseModel):
    # The text currently on screen, which may not be saved yet.
    note: SoapNote
    sections: list[Section] = Field(min_length=1)

    @field_validator("sections")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class SuggestionsOut(BaseModel):
    suggestions: dict[Section, str]


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


class ResubmitRequest(BaseModel):
    # Claims retried at once. Omit to use the server default.
    chunk_size: int | None = Field(default=None, ge=1, le=500)


class ResubmitOut(BaseModel):
    workflow_id: str
    chunk_size: int


class ClaimSummary(BaseModel):
    pending: int = 0
    created: int = 0
    submitted: int = 0
    failed: int = 0


class AuditOut(BaseModel):
    id: int
    actor: str
    action: str
    detail: dict
    created_at: datetime
