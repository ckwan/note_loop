import json

import pytest

from app.errors import LLMOutputError
from app.llm import StubProvider
from app.review import build_review_graph, resume_review, start_review

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."

CLEAN = {
    "subjective": "Client reports poor sleep and anxiety before work meetings.",
    "objective": "Client was tearful and fidgeting.",
    "assessment": "Anxiety symptoms have increased this week.",
    "plan": "Continue weekly sessions and start a sleep log.",
}


def make_graph(provider=None):
    return build_review_graph(provider or StubProvider())


def with_note(**changes):
    return {**CLEAN, **changes}


def kinds(result):
    return sorted((f["section"], f["kind"]) for f in result.findings)


def test_clean_note_is_ready_without_pausing():
    result = start_review(make_graph(), "t", RAW, CLEAN)
    assert result.status == "ready"
    assert result.findings == []
    assert result.note == CLEAN


def test_problems_pause_the_review_for_the_therapist():
    note = with_note(objective="Not documented.", assessment="Likely depression. Anxiety is up.")
    result = start_review(make_graph(), "t", RAW, note)

    assert result.status == "paused"
    assert kinds(result) == [("assessment", "ungrounded"), ("objective", "not_documented")]
    # Each finding says who raised it.
    assert {(f["kind"], f["source"]) for f in result.findings} == {
        ("ungrounded", "grounding"),
        ("not_documented", "billing"),
    }
    # Only the problem that can be fixed without new facts gets a proposed fix.
    assert result.fixes == {"assessment": "Anxiety is up."}


def test_accepting_a_fix_changes_the_note_and_finishes_clean():
    note = with_note(assessment="Likely depression. Anxiety is up.")
    graph = make_graph()
    start_review(graph, "t", RAW, note)

    result = resume_review(graph, "t", {"assessment": "accept"})

    assert result.status == "ready"
    assert result.note["assessment"] == "Anxiety is up."
    assert result.round == 1


def test_dismissed_findings_stay_quiet_in_later_rounds():
    note = with_note(objective="Not documented.")
    graph = make_graph()
    start_review(graph, "t", RAW, note)

    result = resume_review(graph, "t", {"objective": "dismiss"})

    assert result.status == "ready"
    assert result.note["objective"] == "Not documented."  # left as written


def test_therapist_edit_wins_over_a_proposed_fix():
    note = with_note(assessment="Likely depression. Anxiety is up.")
    graph = make_graph()
    start_review(graph, "t", RAW, note)

    result = resume_review(
        graph,
        "t",
        {"assessment": "accept"},
        edits={"assessment": "Anxiety is elevated; sleep is poor."},
    )

    assert result.status == "ready"
    assert result.note["assessment"] == "Anxiety is elevated; sleep is poor."


def test_typing_text_for_an_empty_section_clears_the_finding():
    graph = make_graph()
    start_review(graph, "t", RAW, with_note(objective="Not documented."))

    result = resume_review(graph, "t", {}, edits={"objective": "Client appeared tired."})

    assert result.status == "ready"
    assert result.note["objective"] == "Client appeared tired."


def test_review_stops_after_the_round_limit():
    note = with_note(objective="Not documented.")
    graph = make_graph()
    result = start_review(graph, "t", RAW, note, max_rounds=2)

    # Answering without dismissing or fixing anything leaves the problem in place.
    result = resume_review(graph, "t", {})
    assert result.status == "paused" and result.round == 1
    result = resume_review(graph, "t", {})
    assert result.status == "needs_attention" and result.round == 2
    assert kinds(result) == [("objective", "not_documented")]


def test_risk_language_must_be_documented():
    raw = RAW + " She mentioned thoughts of self harm."
    result = start_review(make_graph(), "t", raw, CLEAN)

    assert kinds(result) == [("assessment", "safety_undocumented")]
    assert "risk" in result.fixes["assessment"].lower()


def test_documented_risk_is_not_flagged():
    raw = RAW + " She mentioned thoughts of self harm."
    note = with_note(assessment="Anxiety is up. Self harm thoughts reported; safety plan reviewed.")
    assert start_review(make_graph(), "t", raw, note).status == "ready"


def test_reviews_are_kept_apart_by_thread_id():
    graph = make_graph()
    start_review(graph, "a", RAW, with_note(objective="Not documented."))
    start_review(graph, "b", RAW, CLEAN)

    resumed = resume_review(graph, "a", {"objective": "dismiss"})
    assert resumed.status == "ready"
    with pytest.raises(ValueError, match="not waiting"):
        resume_review(graph, "b", {})


def test_resume_rejects_bad_input():
    graph = make_graph()
    start_review(graph, "t", RAW, with_note(objective="Not documented."))

    with pytest.raises(ValueError, match="Unknown section"):
        resume_review(graph, "t", {"diagnosis": "accept"})
    with pytest.raises(ValueError, match="Unknown decision"):
        resume_review(graph, "t", {"objective": "maybe"})
    with pytest.raises(ValueError, match="cannot be empty"):
        resume_review(graph, "t", {}, edits={"objective": "  "})


def test_resume_without_a_paused_review_fails():
    with pytest.raises(ValueError, match="not waiting"):
        resume_review(make_graph(), "never-started", {})


class FlakyReviewer(StubProvider):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def review_note(self, raw_input, note):
        self.calls += 1
        return self.replies.pop(0)


def test_one_bad_reply_from_the_model_is_retried():
    good = json.dumps({"findings": []})
    provider = FlakyReviewer(["```json\n{broken", good])
    result = start_review(build_review_graph(provider, reviewers=("general",)), "t", RAW, CLEAN)

    assert result.status == "ready"
    assert provider.calls == 2


def test_two_bad_replies_raise_an_llm_error():
    provider = FlakyReviewer(["nope", "still nope"])
    with pytest.raises(LLMOutputError):
        start_review(build_review_graph(provider, reviewers=("general",)), "t", RAW, CLEAN)


def test_a_malformed_finding_counts_as_a_bad_reply():
    bad = json.dumps({"findings": [{"section": "diagnosis", "kind": "other", "message": "x"}]})
    provider = FlakyReviewer([bad, bad])
    with pytest.raises(LLMOutputError):
        start_review(build_review_graph(provider, reviewers=("general",)), "t", RAW, CLEAN)


class OverreachingFixer(StubProvider):
    def propose_fixes(self, raw_input, note, findings):
        return json.dumps({"plan": "Rewrite of a section nobody flagged."})


def test_a_fix_for_an_unflagged_section_is_rejected():
    note = with_note(objective="Not documented.")
    graph = build_review_graph(OverreachingFixer())
    with pytest.raises(LLMOutputError):
        start_review(graph, "t", RAW, note)


def test_guardrails_catch_what_a_blind_reviewer_misses():
    """A model reviewer that finds nothing must not let an invented drug through."""

    class BlindReviewer(StubProvider):
        def review_as(self, role, raw_input, note):
            return json.dumps({"findings": []})

    note = with_note(plan="Start sertraline 50 mg.")
    result = start_review(make_graph(BlindReviewer()), "blind", RAW, note)
    assert result.status == "paused"
    assert [(f["section"], f["kind"], f["source"]) for f in result.findings] == [
        ("plan", "ungrounded", "guardrail")
    ]
