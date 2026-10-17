import json

from app.config import get_settings
from app.llm import STUB_MARKER
from app.main import app
from app.routes import get_llm_provider

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."
SHORTHAND = {
    "subjective": "poor sleep, anxious, work stress",
    "objective": "tearful; fidgeting",
    "assessment": "anxiety up",
    "plan": "weekly sessions, sleep log",
}


def make_draft(client, session_id=1):
    return client.post(f"/sessions/{session_id}/draft", json={"raw_input": RAW})


def suggest(client, sections, note=None, session_id=1):
    return client.post(
        f"/sessions/{session_id}/note/suggest",
        json={"note": note or SHORTHAND, "sections": sections},
    )


def test_suggests_wording_for_the_requested_sections_only(client):
    make_draft(client)
    res = suggest(client, ["subjective", "plan"])

    assert res.status_code == 200
    suggestions = res.json()["suggestions"]
    assert set(suggestions) == {"subjective", "plan"}
    assert suggestions["subjective"].startswith(STUB_MARKER)
    assert "Poor sleep; anxious; work stress." in suggestions["subjective"]


def test_suggestions_do_not_change_the_saved_note(client):
    saved = make_draft(client).json()["final_note"]
    suggest(client, ["subjective", "objective", "assessment", "plan"])
    assert client.get("/sessions/1/note").json()["final_note"] == saved


def test_suggestion_is_recorded_in_the_audit_trail(client):
    make_draft(client)
    suggest(client, ["plan", "plan", "subjective"])  # duplicates are ignored

    audit = client.get("/sessions/1/note/audit").json()
    event = audit[-1]
    assert event["action"] == "suggestion_generated"
    assert event["detail"] == {"sections": ["plan", "subjective"], "attempts": 1}


def test_text_that_is_already_reworded_comes_back_unchanged(client):
    make_draft(client)
    reworded = dict(SHORTHAND, plan=f"{STUB_MARKER} Weekly sessions.")
    res = suggest(client, ["plan"], note=reworded)
    assert res.json()["suggestions"]["plan"] == f"{STUB_MARKER} Weekly sessions."


def test_unknown_section_is_rejected(client):
    make_draft(client)
    assert suggest(client, ["diagnosis"]).status_code == 422
    assert suggest(client, []).status_code == 422


def test_empty_section_text_is_rejected(client):
    make_draft(client)
    res = suggest(client, ["plan"], note=dict(SHORTHAND, plan=""))
    assert res.status_code == 422


def test_no_note_returns_404(client):
    assert suggest(client, ["plan"]).status_code == 404
    assert suggest(client, ["plan"], session_id=999).status_code == 404


def test_signed_note_cannot_get_suggestions(client):
    make_draft(client)
    client.post("/sessions/1/note/sign")
    assert suggest(client, ["plan"]).status_code == 409


def test_invalid_llm_output_returns_502_and_records_nothing(client):
    class BadProvider:
        def suggest_wording(self, sections):
            return "not json"

    make_draft(client)
    app.dependency_overrides[get_llm_provider] = lambda: BadProvider()
    assert suggest(client, ["plan"]).status_code == 502

    actions = [e["action"] for e in client.get("/sessions/1/note/audit").json()]
    assert "suggestion_generated" not in actions


def test_missing_section_in_the_reply_counts_as_invalid(client):
    class PartialProvider:
        def suggest_wording(self, sections):
            return json.dumps({"plan": "A plan."})  # asked for two sections

    make_draft(client)
    app.dependency_overrides[get_llm_provider] = lambda: PartialProvider()
    assert suggest(client, ["plan", "subjective"]).status_code == 502


def test_one_bad_reply_is_retried(client):
    class FlakyProvider:
        calls = 0

        def suggest_wording(self, sections):
            FlakyProvider.calls += 1
            if FlakyProvider.calls == 1:
                return "```json\n{broken"
            return json.dumps({"plan": "Weekly therapy sessions will continue."})

    make_draft(client)
    app.dependency_overrides[get_llm_provider] = lambda: FlakyProvider()
    res = suggest(client, ["plan"])
    assert res.status_code == 200
    assert res.json()["suggestions"] == {"plan": "Weekly therapy sessions will continue."}


def test_suggestions_are_rate_limited_per_therapist(client, monkeypatch):
    monkeypatch.setattr("app.ratelimit._now", lambda: 2_000_000.0)
    monkeypatch.setattr(get_settings(), "suggest_rate_limit_per_minute", 2)
    make_draft(client)

    assert suggest(client, ["plan"]).status_code == 200
    assert suggest(client, ["plan"]).status_code == 200
    res = suggest(client, ["plan"])
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) >= 1


def test_saving_an_accepted_suggestion_is_marked_ai_assisted(client):
    base = make_draft(client).json()["final_note"]
    edited = dict(base, plan=f"{STUB_MARKER} Weekly sessions.", objective="Tearful.")

    res = client.put(
        "/sessions/1/note",
        json={"final_note": edited, "ai_assisted_sections": ["plan", "assessment"]},
    )
    assert res.status_code == 200

    event = client.get("/sessions/1/note/audit").json()[-1]
    assert event["action"] == "edited"
    # Only sections that were actually edited can be marked. "assessment" was not.
    assert event["detail"] == {"sections": ["objective", "plan"], "ai_assisted": ["plan"]}


def test_plain_edits_have_no_ai_assisted_key(client):
    base = make_draft(client).json()["final_note"]
    client.put("/sessions/1/note", json={"final_note": dict(base, plan="By hand.")})
    event = client.get("/sessions/1/note/audit").json()[-1]
    assert event["detail"] == {"sections": ["plan"]}


def test_text_that_already_reads_formally_comes_back_unchanged(client):
    make_draft(client)
    formal = dict(SHORTHAND, plan="Weekly sessions will continue.")
    res = suggest(client, ["plan"], note=formal)
    assert res.json()["suggestions"]["plan"] == "Weekly sessions will continue."
