import json

import pytest

from app.db import SessionLocal
from app.models import Note
from app.routes import get_llm_provider
from app.main import app

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."


def draft(client, session_id=1):
    return client.post(f"/sessions/{session_id}/draft", json={"raw_input": RAW})


def test_lists_seeded_sessions(client):
    res = client.get("/sessions")
    assert res.status_code == 200
    assert len(res.json()) == 6
    assert res.json()[0]["note_status"] is None


def test_draft_edit_sign_flow(client):
    res = draft(client)
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "draft"
    assert body["ai_draft"] == body["final_note"]
    assert body["changed_sections"] == []
    original_draft = body["ai_draft"]

    edited = dict(body["final_note"], plan="Continue weekly sessions. Sleep log.")
    res = client.put("/sessions/1/note", json={"final_note": edited})
    assert res.status_code == 200
    assert res.json()["changed_sections"] == ["plan"]
    assert res.json()["ai_draft"] == original_draft

    res = client.post("/sessions/1/note/sign")
    assert res.status_code == 200
    assert res.json()["status"] == "signed"
    assert res.json()["signed_at"] is not None

    audit = client.get("/sessions/1/note/audit").json()
    assert [e["action"] for e in audit] == ["draft_generated", "edited", "signed"]
    assert audit[1]["detail"] == {"sections": ["plan"]}

    listed = client.get("/sessions/1").json()
    assert listed["note_status"] == "signed"


def test_second_draft_is_rejected(client):
    assert draft(client).status_code == 201
    assert draft(client).status_code == 409


def test_signed_note_cannot_be_edited_or_signed_again(client):
    note = draft(client).json()
    client.post("/sessions/1/note/sign")
    res = client.put("/sessions/1/note", json={"final_note": note["final_note"]})
    assert res.status_code == 409
    assert client.post("/sessions/1/note/sign").status_code == 409


def test_unknown_session_returns_404(client):
    assert draft(client, session_id=999).status_code == 404
    assert client.get("/sessions/1/note").status_code == 404


def test_short_input_is_rejected(client):
    res = client.post("/sessions/1/draft", json={"raw_input": "too short"})
    assert res.status_code == 422


def test_invalid_llm_output_returns_502_and_saves_nothing(client):
    class BadProvider:
        def draft_soap(self, raw_input):
            return "this is not json"

    app.dependency_overrides[get_llm_provider] = lambda: BadProvider()
    assert draft(client).status_code == 502
    assert client.get("/sessions/1/note").status_code == 404


def test_retry_recovers_from_one_bad_response(client):
    good = json.dumps(
        {"subjective": "s", "objective": "o", "assessment": "a", "plan": "p"}
    )

    class FlakyProvider:
        calls = 0

        def draft_soap(self, raw_input):
            FlakyProvider.calls += 1
            return "```json\n{broken" if FlakyProvider.calls == 1 else good

    app.dependency_overrides[get_llm_provider] = lambda: FlakyProvider()
    assert draft(client).status_code == 201
    audit = client.get("/sessions/1/note/audit").json()
    assert audit[0]["detail"] == {"attempts": 2}


def test_ai_draft_cannot_be_changed_in_the_database(client):
    draft(client)
    with SessionLocal() as db:
        note = db.query(Note).one()
        note.ai_draft = {
            "subjective": "x",
            "objective": "x",
            "assessment": "x",
            "plan": "x",
        }
        with pytest.raises(ValueError):
            db.commit()
