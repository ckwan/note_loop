"""What a model reply must look like, written once as Pydantic models.

Each model says the shape of one kind of reply. Pydantic checks it and says exactly
what is wrong, and the service layer retries once when it is not valid.
"""

import json
import re
from typing import Annotated

from pydantic import (
    BaseModel,
    RootModel,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    model_validator,
)

from .schemas import Finding, Section, SoapNote

# Text with blank space trimmed and at least one character left.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def extract_json_object(text: str) -> dict:
    """Pull one JSON object out of model text. Raises ValueError on any failure."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    obj = json.loads(cleaned[start : end + 1])
    if not isinstance(obj, dict):
        raise ValueError("Expected a JSON object")
    return obj


class FindingsReply(BaseModel):
    """A review: {"findings": [{"section": ..., "kind": ..., "message": ...}]}."""

    findings: list[Finding]


class SuggestionsReply(RootModel[dict[Section, Text]]):
    """A wording suggestion. Needs non empty text for every section that was asked for.

    Pass the asked for sections as validation context: {"expected": [...]}. Other keys
    in the reply are ignored.
    """

    @model_validator(mode="before")
    @classmethod
    def _only_expected(cls, data, info: ValidationInfo):
        expected = (info.context or {}).get("expected", [])
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        missing = [name for name in expected if name not in data]
        if missing:
            raise ValueError(f"Missing suggestion for {', '.join(missing)}")
        return {name: data[name] for name in expected}


class FixesReply(RootModel[dict[Section, Text]]):
    """Proposed fixes. Only sections that were flagged may appear.

    Pass the flagged sections as validation context: {"allowed": {...}}.
    """

    @model_validator(mode="before")
    @classmethod
    def _only_allowed(cls, data, info: ValidationInfo):
        allowed = (info.context or {}).get("allowed", set())
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        extra = [name for name in data if name not in allowed]
        if extra:
            raise ValueError(f"Fix for a section that was not flagged: {', '.join(extra)}")
        return data


def parse_soap_json(text: str) -> SoapNote:
    return SoapNote.model_validate(extract_json_object(text))


def parse_findings(text: str) -> list[Finding]:
    return FindingsReply.model_validate(extract_json_object(text)).findings


def parse_suggestions(text: str, expected: list[str]) -> dict[str, str]:
    reply = SuggestionsReply.model_validate(
        extract_json_object(text), context={"expected": expected}
    )
    return dict(reply.root)


def parse_fixes(text: str, allowed: set[str]) -> dict[str, str]:
    reply = FixesReply.model_validate(extract_json_object(text), context={"allowed": allowed})
    return dict(reply.root)


def describe_error(exc: Exception) -> str:
    """A short reason for a rejected reply that is safe to log.

    Pydantic's own message repeats the value it rejected, which can be note text. This
    keeps only where the problem was and what kind it was.
    """
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['type']}" for e in exc.errors()
        )
    if isinstance(exc, json.JSONDecodeError):
        return "invalid JSON"
    return type(exc).__name__
