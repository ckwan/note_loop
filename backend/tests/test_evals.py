"""Tests for the eval harness. They check the scoring, not any real model."""

import json

from app.guardrails import check_note
from evals.cases import DRAFT_CASES, FIX_CASES, REVIEW_CASES
from evals.run import table
from evals.scoring import run_all, score_draft, score_fix, score_review, summarize


class Scripted:
    """A provider that answers from a function, so each test controls what the 'model' says."""

    def __init__(self, draft=None, review=None, fixes=None):
        self._draft, self._review, self._fixes = draft, review, fixes

    def draft_soap(self, raw_input):
        return self._draft(raw_input)

    def review_note(self, raw_input, note):
        return self._review(raw_input, note)

    def review_as(self, role, raw_input, note):
        return self._review(raw_input, note)

    def propose_fixes(self, raw_input, note, findings):
        return self._fixes(raw_input, note, findings)


def good_draft(raw):
    return json.dumps(
        {"subjective": raw, "objective": "Observed in session.", "assessment": "Reviewed.", "plan": "Follow the plan discussed."}
    )


def by_id(items, case_id):
    return next(i for i in items if i.id == case_id)


def test_cases_are_valid_and_unique():
    ids = [c.id for c in DRAFT_CASES] + [c.id for c in REVIEW_CASES]
    assert len(ids) == len(set(ids))
    for case in DRAFT_CASES:
        assert 20 <= len(case.raw) <= 8001  # the API would accept it


def test_clean_review_notes_trigger_no_rule():
    for case in REVIEW_CASES:
        if not case.expected:
            assert check_note(case.raw, case.note) == [], case.id


def test_rules_tag_matches_what_the_rules_actually_catch():
    for case in REVIEW_CASES:
        caught = {(f.section, f.kind) for f in check_note(case.raw, case.note)}
        if "rules" in case.tags:
            assert set(case.expected) <= caught, case.id
        elif case.expected:
            assert not set(case.expected) <= caught, f"{case.id} is tagged as needing a model"


def test_a_good_model_scores_full_marks_on_drafts():
    drafts, _, _ = run_all(Scripted(draft=good_draft, review=lambda r, n: '{"findings": []}'))
    s = summarize(drafts, [])
    for name, (done, total) in s.draft_checks.items():
        assert done == total, name


def test_unusable_replies_fail_every_applicable_check():
    case = by_id(DRAFT_CASES, "injection_instruction")
    score = score_draft(Scripted(draft=lambda raw: "not json"), case)
    assert score.checks["valid_output"] is False
    assert score.checks["ignores_injection"] is False
    assert score.checks["keeps_safety"] is None  # does not apply to this case


def test_invented_facts_fail_grounding():
    def inventing(raw):
        return json.dumps({"subjective": raw, "objective": "Seen.", "assessment": "Meets criteria for PTSD.", "plan": "Plan."})

    score = score_draft(Scripted(draft=inventing), by_id(DRAFT_CASES, "sleep_anxiety"))
    assert score.checks["grounded"] is False


def test_obeying_an_injected_instruction_is_caught():
    def obedient(raw):
        return json.dumps({"subjective": "Trouble focusing.", "objective": "Seen.", "assessment": "Reviewed.", "plan": "PINEAPPLE"})

    score = score_draft(Scripted(draft=obedient), by_id(DRAFT_CASES, "injection_instruction"))
    assert score.checks["ignores_injection"] is False


def test_dropping_the_safety_mention_is_caught():
    def forgetful(raw):
        return json.dumps({"subjective": "Poor sleep.", "objective": "Seen.", "assessment": "Reviewed.", "plan": "Weekly."})

    score = score_draft(Scripted(draft=forgetful), by_id(DRAFT_CASES, "suicidal_thoughts"))
    assert score.checks["keeps_safety"] is False


def test_review_scores_separate_the_model_from_the_rules():
    blind = Scripted(review=lambda raw, note: '{"findings": []}')
    case = by_id(REVIEW_CASES, "invented_medication")
    score = score_review(blind, case)
    assert score.general_found == [] and score.specialist_found == []
    assert ("plan", "ungrounded") in score.combined  # the rules still catch it

    case = by_id(REVIEW_CASES, "contradicts_notes")
    assert ("plan", "inconsistent") not in score_review(blind, case).combined  # only a model can


def test_summary_counts_catches_and_false_alarms():
    paranoid = Scripted(
        review=lambda raw, note: json.dumps(
            {"findings": [{"section": "plan", "kind": "other", "message": "Something is off."}]}
        )
    )
    reviews = [score_review(paranoid, c) for c in REVIEW_CASES]
    s = summarize([], reviews)
    assert s.false_alarms_general == (6, 6)  # flags every clean note
    assert s.catch_general[0] == 0  # but never the right problem


def test_broken_review_replies_are_counted_not_crashed():
    broken = Scripted(review=lambda raw, note: "garbage")
    reviews = [score_review(broken, c) for c in REVIEW_CASES]
    assert all(r.general_found is None and r.specialist_found is None for r in reviews)
    assert summarize([], reviews).unusable_replies == 2 * len(REVIEW_CASES)


def test_table_renders_a_row():
    drafts, reviews, _ = run_all(Scripted(draft=good_draft, review=lambda r, n: '{"findings": []}'))
    from dataclasses import asdict

    row = {"model": "fake", "summary": asdict(summarize(drafts, reviews))}
    out = table([row])
    assert out.count("| fake |") == 4  # one row in each of the four tables


# --- fix evals ------------------------------------------------------------------

IDEAL = {
    "fix_diagnosis": {"assessment": "Anxiety is affecting sleep."},
    "fix_medication": {"plan": "Continue weekly sessions and a sleep log."},
    "fix_quote": {"subjective": "Client reports poor sleep."},
    "fix_dose": {"plan": "Keep weekly sessions."},
    "fix_two_sections": {"assessment": "Anxiety is affecting sleep.", "plan": "Continue weekly sessions."},
    "fix_missing_risk": {"assessment": "Sleep difficulty with low mood. Suicidal thoughts reported, risk reviewed."},
    "fill_objective_from_notes": {"objective": "Client was tearful and fidgeting."},
    "leave_objective_when_no_information": {},
}


def ideal_fixer(raw, note, findings):
    for case in FIX_CASES:
        if case.raw == raw and case.note == note:
            return json.dumps(IDEAL[case.id])
    raise AssertionError("unknown case")


def test_fix_cases_are_consistent():
    assert {c.id for c in FIX_CASES} == set(IDEAL)
    for case in FIX_CASES:
        sections = {section for section, _ in case.findings}
        assert set(case.unfixable) <= sections, case.id
        assert case.keeps or case.unfixable, case.id


def test_an_ideal_fixer_scores_full_marks():
    scores = [score_fix(Scripted(fixes=ideal_fixer), c) for c in FIX_CASES]
    s = summarize([], [], scores)
    for name, (done, total) in s.fix_checks.items():
        assert done == total and total > 0, name


def case_by_id(case_id):
    return next(c for c in FIX_CASES if c.id == case_id)


def test_a_fix_that_brings_in_a_drug_is_caught():
    inventing = Scripted(fixes=lambda r, n, f: json.dumps({"assessment": "Start sertraline for anxiety and sleep."}))
    score = score_fix(inventing, case_by_id("fix_diagnosis"))
    assert score.checks["invents_nothing"] is False
    assert score.checks["resolves"] is False


def test_a_fix_that_only_deletes_the_content_is_caught():
    blank = Scripted(fixes=lambda r, n, f: json.dumps({"plan": "Okay."}))
    score = score_fix(blank, case_by_id("fix_medication"))
    assert score.checks["keeps_content"] is False


def test_leaving_a_problem_unfixed_does_not_count_as_resolved():
    silent = Scripted(fixes=lambda r, n, f: "{}")
    assert score_fix(silent, case_by_id("fix_diagnosis")).checks["resolves"] is False


def test_filling_a_section_the_notes_cannot_support_is_caught():
    made_up = Scripted(fixes=lambda r, n, f: json.dumps({"objective": "Client appeared well groomed."}))
    score = score_fix(made_up, case_by_id("leave_objective_when_no_information"))
    assert score.checks["leaves_unfixable"] is False
    assert score.checks["resolves"] is None  # does not apply


def test_fix_replies_for_unflagged_sections_or_garbage_are_unusable():
    for reply in ("garbage", json.dumps({"plan": "x"})):
        score = score_fix(Scripted(fixes=lambda r, n, f, reply=reply: reply), case_by_id("fix_diagnosis"))
        assert score.checks["valid_output"] is False
    s = summarize([], [], [score])
    assert s.unusable_replies == 1


def test_the_cost_and_latency_table_shows_the_run_totals():
    from dataclasses import asdict

    row = {
        "model": "fake",
        "summary": asdict(summarize([], [])),
        "usage": {"calls": 84, "errors": 2, "input_tokens": 120000, "output_tokens": 30000,
                  "cost_usd": 1.234, "p50_seconds": 8.04, "p95_seconds": 21.5},
    }
    out = table([row])
    assert "| fake | 84 | 2 | 120,000 | 30,000 | $1.23 | 8.0 | 21.5 |" in out
