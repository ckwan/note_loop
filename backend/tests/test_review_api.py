import json

from app.config import get_settings
from app.main import app
from app.routes import get_llm_provider

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."
CLEAN = {
    "subjective": "Client reports poor sleep and anxiety before work meetings.",
    "objective": "Client was tearful and fidgeting.",
    "assessment": "Anxiety symptoms have increased this week.",
    "plan": "Continue weekly sessions and start a sleep log.",
}
PROBLEM = {**CLEAN, "objective": "Not documented.", "assessment": "Likely depression. Anxiety is up."}


def make_draft(client, session_id=1):
    return client.post(f"/sessions/{session_id}/draft", json={"raw_input": RAW})


def review(client, note=None, session_id=1):
    return client.post(f"/sessions/{session_id}/note/review", json={"note": note or CLEAN})


def answer(client, decisions=None, edits=None, session_id=1):
    return client.post(
        f"/sessions/{session_id}/note/review/resume",
        json={"decisions": decisions or {}, "edits": edits or {}},
    )


def audit(client):
    return client.get("/sessions/1/note/audit").json()


def test_clean_note_is_ready(client):
    make_draft(client)
    res = review(client)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready"
    assert body["findings"] == []
    assert body["note"] == CLEAN


def test_problems_pause_with_findings_and_fixes(client):
    make_draft(client)
    body = review(client, PROBLEM).json()

    assert body["status"] == "paused"
    assert {(f["section"], f["kind"]) for f in body["findings"]} == {
        ("objective", "not_documented"),
        ("assessment", "ungrounded"),
    }
    assert body["fixes"] == {"assessment": "Anxiety is up."}


def test_accepting_fixes_finishes_the_review(client):
    make_draft(client)
    review(client, PROBLEM)
    res = answer(client, {"assessment": "accept", "objective": "dismiss"})

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready"
    assert body["note"]["assessment"] == "Anxiety is up."
    assert body["round"] == 1


def test_review_never_changes_the_saved_note(client):
    saved = make_draft(client).json()["final_note"]
    review(client, PROBLEM)
    answer(client, {"assessment": "accept", "objective": "dismiss"})
    assert client.get("/sessions/1/note").json()["final_note"] == saved


def test_get_returns_the_latest_review_state(client):
    make_draft(client)
    review(client, PROBLEM)
    body = client.get("/sessions/1/note/review").json()
    assert body["status"] == "paused"

    answer(client, {"assessment": "accept", "objective": "dismiss"})
    assert client.get("/sessions/1/note/review").json()["status"] == "ready"


def test_get_before_any_review_is_404(client):
    make_draft(client)
    assert client.get("/sessions/1/note/review").status_code == 404


def test_starting_again_begins_a_fresh_review(client):
    make_draft(client)
    review(client, PROBLEM)
    second = review(client, CLEAN).json()
    assert second["status"] == "ready"
    assert client.get("/sessions/1/note/review").json()["status"] == "ready"


def test_review_events_are_audited_without_finding_text(client):
    make_draft(client)
    review(client, PROBLEM)
    answer(client, {"assessment": "accept", "objective": "dismiss"}, edits={})

    events = [e for e in audit(client) if e["action"].startswith("review_")]
    assert [e["action"] for e in events] == ["review_started", "review_answered"]

    started, answered = events[0]["detail"], events[1]["detail"]
    assert started["status"] == "paused"
    assert {(f["section"], f["kind"]) for f in started["findings"]} == {
        ("objective", "not_documented"),
        ("assessment", "ungrounded"),
    }
    assert all("message" not in f for f in started["findings"])
    assert answered["accepted"] == ["assessment"]
    assert answered["dismissed"] == ["objective"]
    assert answered["status"] == "ready"


def test_signed_note_cannot_be_reviewed(client):
    make_draft(client)
    client.post("/sessions/1/note/sign")
    assert review(client).status_code == 409


def test_answering_a_signed_note_fails(client):
    make_draft(client)
    review(client, PROBLEM)
    client.post("/sessions/1/note/sign")
    assert answer(client, {"objective": "dismiss"}).status_code == 409


def test_answer_without_a_review_is_409(client):
    make_draft(client)
    assert answer(client).status_code == 409


def test_answer_after_the_review_finished_is_409(client):
    make_draft(client)
    review(client, CLEAN)
    assert answer(client).status_code == 409


def test_no_note_returns_404(client):
    assert review(client).status_code == 404
    assert review(client, session_id=999).status_code == 404


def test_bad_input_is_rejected(client):
    make_draft(client)
    review(client, PROBLEM)
    assert answer(client, {"diagnosis": "accept"}).status_code == 422
    assert answer(client, {"objective": "maybe"}).status_code == 422
    assert answer(client, edits={"objective": "  "}).status_code == 422
    assert client.post("/sessions/1/note/review", json={"note": {"subjective": "x"}}).status_code == 422


def test_invalid_llm_output_returns_502(client):
    class BadReviewer:
        def review_as(self, role, raw_input, note):
            return "not json"

    make_draft(client)
    app.dependency_overrides[get_llm_provider] = lambda: BadReviewer()
    assert review(client).status_code == 502
    assert not [e for e in audit(client) if e["action"] == "review_started"]


def test_a_model_that_finds_nothing_ends_the_review(client):
    class Happy:
        def review_as(self, role, raw_input, note):
            return json.dumps({"findings": []})

    make_draft(client)
    app.dependency_overrides[get_llm_provider] = lambda: Happy()
    assert review(client, PROBLEM).json()["status"] == "ready"


def test_reviews_are_rate_limited_per_therapist(client, monkeypatch):
    monkeypatch.setattr("app.ratelimit._now", lambda: 3_000_000.0)
    monkeypatch.setattr(get_settings(), "review_rate_limit_per_minute", 2)
    make_draft(client)

    assert review(client).status_code == 200
    assert review(client).status_code == 200
    res = review(client)
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) >= 1


def run_to_needs_attention(client):
    make_draft(client)
    review(client, {**CLEAN, "objective": "Not documented."})
    answer(client)  # nothing dismissed, so the problem comes back
    return answer(client)


def test_slack_is_told_when_a_review_ends_with_open_findings(client, notifier):
    res = run_to_needs_attention(client)

    assert res.json()["status"] == "needs_attention"
    assert len(notifier.sent) == 1
    message = notifier.sent[0]
    assert message.title == "Review finished with open findings"
    assert message.fields["Open findings"] == "objective: not_documented"
    assert message.fields["Rounds"] == "2"


def test_slack_is_quiet_for_paused_and_clean_reviews(client, notifier):
    make_draft(client)
    review(client, {**CLEAN, "objective": "Not documented."})  # paused
    review(client, CLEAN)  # ready
    assert notifier.sent == []


def test_a_failing_slack_never_breaks_the_review(client):
    from app.routes import get_notifier

    class Broken:
        def send(self, notification):
            raise RuntimeError("slack is down")

    app.dependency_overrides[get_notifier] = lambda: Broken()
    res = run_to_needs_attention(client)
    assert res.status_code == 200
    assert res.json()["status"] == "needs_attention"
