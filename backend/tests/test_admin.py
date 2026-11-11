import json

from app.services import admin
from tests.test_review_api import CLEAN, PROBLEM, answer, make_draft, review


def overview(client):
    res = client.get("/admin/overview")
    assert res.status_code == 200
    return res.json()


def test_empty_system_has_zeros(client):
    out = overview(client)
    assert out["review"]["reviews_started"] == 0
    assert out["review"]["signed_without_review"] == 0
    assert out["edits"]["signed_notes"] == 0
    assert out["review"]["by_reviewer"] == {}


def test_review_counts_reviewers_and_decisions(client):
    make_draft(client)
    review(client, PROBLEM)
    answer(client, {"assessment": "accept", "objective": "dismiss"})

    out = overview(client)["review"]
    assert out["reviews_started"] == 1
    assert out["review_rounds"] >= 2  # the start and the answer
    assert out["by_kind"]["not_documented"] >= 1
    assert out["by_kind"]["ungrounded"] >= 1

    raised = sum(r["raised"] for r in out["by_reviewer"].values())
    assert raised >= 2
    assert sum(r["accepted"] for r in out["by_reviewer"].values()) >= 1
    assert sum(r["dismissed"] for r in out["by_reviewer"].values()) >= 1
    assert out["recent"][0]["findings"] == 2


def test_signing_without_a_review_is_counted(client):
    make_draft(client)
    client.post("/sessions/1/note/sign")
    out = overview(client)["review"]
    assert out["signed_notes"] == 1
    assert out["signed_without_review"] == 1
    assert out["signed_with_open_findings"] == 0


def test_signing_after_a_clean_review_is_not_flagged(client):
    make_draft(client)
    review(client, CLEAN)
    client.post("/sessions/1/note/sign")
    out = overview(client)["review"]
    assert out["signed_without_review"] == 0
    assert out["signed_with_open_findings"] == 0


def test_signing_with_problems_still_open_is_flagged(client):
    make_draft(client)
    review(client, PROBLEM)  # paused with findings, never answered
    client.post("/sessions/1/note/sign")
    out = overview(client)["review"]
    assert out["signed_without_review"] == 0
    assert out["signed_with_open_findings"] == 1


def test_admin_output_has_no_note_text(client):
    make_draft(client)
    review(client, PROBLEM)
    text = json.dumps(overview(client))
    assert "Likely depression" not in text
    assert "sleep" not in text.lower()


def test_eval_results_read_newest_per_model(tmp_path, monkeypatch):
    def write(name, model, date, rate):
        (tmp_path / name).write_text(json.dumps(
            {"provider": "ollama", "model": model, "date": date, "summary": {"x": rate}, "usage": {}}
        ))

    write("a.json", "m1", "2026-10-01T00:00:00", 1)
    write("b.json", "m1", "2026-10-02T00:00:00", 2)
    write("c.json", "m2", "2026-10-03T00:00:00", 3)
    (tmp_path / "bad.json").write_text("not json")
    monkeypatch.setattr(admin, "RESULTS_DIR", tmp_path)

    rows = admin.eval_results()
    assert [(r["model"], r["summary"]["x"]) for r in rows] == [("m2", 3), ("m1", 2)]


def test_missing_eval_folder_is_just_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "RESULTS_DIR", tmp_path / "nope")
    assert admin.eval_results() == []
