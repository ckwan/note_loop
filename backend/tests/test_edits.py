import pytest

from app import metrics
from app.edits import edit_rate, edit_stats, summarize

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."


def draft(client, session_id=1):
    return client.post(f"/sessions/{session_id}/draft", json={"raw_input": RAW})


def hist_sum(name, **labels):
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


# --- the measure ------------------------------------------------------------------


def test_identical_text_is_zero_and_replaced_text_is_one():
    assert edit_rate("same words here", "same words here") == 0.0
    assert edit_rate("one two three", "four five six") == 1.0


def test_empty_on_both_sides_is_zero_not_a_divide_error():
    assert edit_rate("", "") == 0.0


def test_partial_edit_is_a_share_of_the_longer_text():
    # one of four words replaced
    assert edit_rate("a b c d", "a b c z") == 0.25
    # two words added to a four word text, measured against the longer six
    assert edit_rate("a b c d", "a b c d e f") == pytest.approx(0.333, abs=0.001)


def test_stats_are_per_section_and_weighted_overall():
    draft_note = {"subjective": "a b c d", "objective": "x y", "assessment": "m n", "plan": "p q"}
    final = dict(draft_note, subjective="a b c z", plan="r s")
    stats = edit_stats(draft_note, final)
    assert stats["section_edit_rates"] == {
        "subjective": 0.25, "objective": 0.0, "assessment": 0.0, "plan": 1.0
    }
    # 1 + 2 changed words out of 4 + 2 + 2 + 2
    assert stats["edit_rate"] == 0.3


def test_summary_of_nothing_is_zeros():
    out = summarize([])
    assert out["signed_notes"] == 0 and out["mean_edit_rate"] == 0.0


# --- on the API ----------------------------------------------------------------------


def test_note_reports_edit_rate_live_and_sign_audits_numbers_only(client):
    body = draft(client).json()
    assert body["edit_rate"] == 0.0

    edited = dict(body["final_note"], plan="Completely different plan words here now.")
    res = client.put("/sessions/1/note", json={"final_note": edited}).json()
    assert 0 < res["edit_rate"] <= 1
    assert res["section_edit_rates"]["plan"] > 0
    assert res["section_edit_rates"]["subjective"] == 0.0

    before = hist_sum("noteloop_note_edit_rate_count")
    client.post("/sessions/1/note/sign")
    assert hist_sum("noteloop_note_edit_rate_count") == before + 1

    signed = client.get("/sessions/1/note/audit").json()[-1]
    assert signed["action"] == "signed"
    assert signed["detail"]["edit_rate"] == res["edit_rate"]
    assert signed["detail"]["section_edit_rates"]["plan"] == res["section_edit_rates"]["plan"]
    # numbers only, so no note text leaks into the audit row
    assert "Completely different" not in str(signed["detail"])


def test_unchanged_signature_counts_as_zero_edit(client):
    draft(client)
    client.post("/sessions/1/note/sign")
    audit = client.get("/sessions/1/note/audit").json()
    assert audit[-1]["detail"]["edit_rate"] == 0.0


def test_summary_endpoint_counts_only_signed_notes(client):
    draft(client, 1)
    draft(client, 2)
    # session 1: signed untouched. session 2: edited but never signed.
    client.post("/sessions/1/note/sign")
    body2 = client.get("/sessions/2/note").json()
    client.put("/sessions/2/note", json={"final_note": dict(body2["final_note"], plan="New plan.")})

    out = client.get("/stats/edits").json()
    assert out["signed_notes"] == 1
    assert out["mean_edit_rate"] == 0.0
    assert out["signed_unchanged_share"] == 1.0

    client.post("/sessions/2/note/sign")
    out = client.get("/stats/edits").json()
    assert out["signed_notes"] == 2
    assert out["mean_edit_rate"] > 0
    assert out["signed_unchanged_share"] == 0.5
    assert out["by_section"]["plan"] > 0


def test_summary_can_be_filtered_by_therapist(client):
    draft(client, 1)
    client.post("/sessions/1/note/sign")
    from app.db import SessionLocal
    from app.models import TherapySession

    with SessionLocal() as db:
        owner_id = db.get(TherapySession, 1).therapist_id
    assert client.get(f"/stats/edits?therapist_id={owner_id}").json()["signed_notes"] == 1
    assert client.get("/stats/edits?therapist_id=999").json()["signed_notes"] == 0
