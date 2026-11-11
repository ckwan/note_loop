import json
import logging

import pytest
from pydantic import ValidationError

from app.replies import (
    FindingsReply,
    FixesReply,
    SuggestionsReply,
    describe_error,
    extract_json_object,
    parse_findings,
    parse_fixes,
    parse_soap_json,
    parse_suggestions,
)
from app.services.notes import _generate
from app.errors import LLMOutputError

SOAP = {"subjective": "S", "objective": "O", "assessment": "A", "plan": "P"}


def test_json_is_found_inside_fences_and_chatter():
    assert extract_json_object('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    for bad in ("no braces", "[1, 2]", "{broken"):
        with pytest.raises(ValueError):
            extract_json_object(bad)


def test_soap_reply_needs_all_four_nonempty_sections():
    assert parse_soap_json(json.dumps(SOAP)).plan == "P"
    with pytest.raises(ValidationError):
        parse_soap_json(json.dumps({**SOAP, "plan": ""}))
    with pytest.raises(ValidationError):
        parse_soap_json(json.dumps({"subjective": "S"}))


def test_findings_reply_validates_every_finding():
    ok = {"findings": [{"section": "plan", "kind": "other", "message": "m"}]}
    assert parse_findings(json.dumps(ok))[0].section == "plan"
    assert FindingsReply.model_validate({"findings": []}).findings == []
    for bad in (
        {"findings": "none"},
        {"findings": [{"section": "diagnosis", "kind": "other", "message": "m"}]},
        {"findings": [{"section": "plan", "kind": "made_up", "message": "m"}]},
        {"findings": [{"section": "plan", "kind": "other", "message": ""}]},
        {},
    ):
        with pytest.raises(ValueError):
            parse_findings(json.dumps(bad))


def test_suggestions_need_every_requested_section_and_ignore_others():
    reply = json.dumps({"plan": "  Weekly sessions.  ", "extra": ""})
    assert parse_suggestions(reply, ["plan"]) == {"plan": "Weekly sessions."}
    with pytest.raises(ValueError, match="Missing suggestion for objective"):
        parse_suggestions(reply, ["plan", "objective"])
    with pytest.raises(ValueError):
        parse_suggestions(json.dumps({"plan": "   "}), ["plan"])
    with pytest.raises(ValueError):
        parse_suggestions(json.dumps({"plan": 5}), ["plan"])


def test_fixes_may_only_cover_flagged_sections_with_real_text():
    assert parse_fixes(json.dumps({"plan": " New plan. "}), {"plan", "objective"}) == {
        "plan": "New plan."
    }
    assert parse_fixes("{}", {"plan"}) == {}
    with pytest.raises(ValueError, match="not flagged"):
        parse_fixes(json.dumps({"objective": "x"}), {"plan"})
    with pytest.raises(ValueError):
        parse_fixes(json.dumps({"plan": ""}), {"plan"})


def test_models_can_be_used_directly_with_context():
    assert SuggestionsReply.model_validate({"plan": "x"}, context={"expected": ["plan"]}).root
    assert FixesReply.model_validate({}, context={"allowed": set()}).root == {}


def test_error_descriptions_name_the_place_not_the_value():
    secret = "Client Jane Doe said she hates her job"
    try:
        parse_soap_json(json.dumps({**SOAP, "plan": "", "subjective": secret, "assessment": 3}))
    except ValidationError as exc:
        text = describe_error(exc)
    assert "plan: string_too_short" in text and "assessment: string_type" in text
    assert secret not in text
    assert describe_error(json.JSONDecodeError("x", "doc", 0)) == "invalid JSON"
    assert describe_error(KeyError("k")) == "KeyError"


def test_rejected_replies_are_logged_without_note_text(caplog):
    secret = "Client Jane Doe said she hates her job"
    replies = [json.dumps({**SOAP, "subjective": secret, "plan": ""})] * 2
    with caplog.at_level(logging.WARNING, logger="app.services.notes"):
        with pytest.raises(LLMOutputError):
            _generate(lambda: replies.pop(0), parse_soap_json, "SOAP note")
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert "attempt 1 of 2" in messages[0] and "plan: string_too_short" in messages[0]
    assert all(secret not in m for m in messages)
