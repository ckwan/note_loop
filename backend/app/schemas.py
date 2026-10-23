import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_input(text: str) -> str:
    """Drop control characters. Keeps newlines and tabs."""
    return _CONTROL.sub("", text)


SECTIONS = ("subjective", "objective", "assessment", "plan")
Section = Literal["subjective", "objective", "assessment", "plan"]


class SoapNote(BaseModel):
    subjective: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    assessment: str = Field(min_length=1)
    plan: str = Field(min_length=1)


class DraftRequest(BaseModel):
    raw_input: str = Field(min_length=20, max_length=8000)

    @field_validator("raw_input")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = clean_input(value)
        if len(value.strip()) < 20:
            raise ValueError("Session notes are too short after removing blank space")
        return value


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
    # Guardrail findings on the AI draft. Computed on read, so they are never stale.
    draft_warnings: list["Finding"] = Field(default_factory=list)
    id: int
    session_id: int
    status: str
    ai_draft: SoapNote | None
    final_note: SoapNote | None
    changed_sections: list[str]
    # Share of the draft the therapist changed. 0 is as written, 1 is fully rewritten.
    edit_rate: float | None = None
    section_edit_rates: dict[str, float] = Field(default_factory=dict)
    signed_at: datetime | None


class EditSummaryOut(BaseModel):
    signed_notes: int
    mean_edit_rate: float
    median_edit_rate: float
    signed_unchanged_share: float
    by_section: dict[str, float]


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


FindingKind = Literal[
    "not_documented", "ungrounded", "safety_undocumented", "inconsistent", "other"
]


class Finding(BaseModel):
    """One problem found while reviewing a note before signing."""

    section: Section
    kind: FindingKind
    message: str = Field(min_length=1)
    # Which reviewer raised it. "general" until the reviewers are split into roles.
    source: str = "general"


class ReviewStartRequest(BaseModel):
    # The text currently on screen, which may not be saved yet.
    note: SoapNote


class ReviewAnswerRequest(BaseModel):
    # accept: use the proposed fix. dismiss: keep the text and stop flagging it.
    decisions: dict[Section, Literal["accept", "dismiss"]] = Field(default_factory=dict)
    # Text the therapist typed. Wins over a proposed fix for the same section.
    edits: dict[Section, str] = Field(default_factory=dict)

    @field_validator("edits")
    @classmethod
    def _not_blank(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not text.strip() for text in value.values()):
            raise ValueError("An edit cannot be empty")
        return value


class ReviewOut(BaseModel):
    # paused: waiting for the therapist. ready: no problems left.
    # needs_attention: problems remain after the last round.
    status: Literal["paused", "ready", "needs_attention"]
    round: int
    # The note text after any accepted fixes. Not saved. The client saves it.
    note: SoapNote
    findings: list[Finding]
    fixes: dict[Section, str]


NoteOut.model_rebuild()
