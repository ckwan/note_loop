import json
import threading

import pytest

from app.errors import LLMOutputError
from app.llm import ROLES, StubProvider, parse_role_findings
from app.review import build_review_graph, resume_review, start_review
from app.review.supervisor import supervise
from app.schemas import Finding

RAW = "Client reports poor sleep and was tearful. Client mentioned suicidal thoughts twice this week."
NOTE = {
    "subjective": "Client reports poor sleep.",
    "objective": "Client was tearful.",
    "assessment": "Sleep difficulty.",
    "plan": "Weekly sessions.",
}


def f(section, kind, source, message="m"):
    return Finding(section=section, kind=kind, message=message, source=source)


# --- the supervisor on its own -------------------------------------------------


def test_same_problem_from_two_reviewers_becomes_one_finding_with_both_sources():
    merged = supervise(
        {
            "grounding": [f("plan", "ungrounded", "grounding", "from grounding")],
            "guardrail": [f("plan", "ungrounded", "guardrail", "from rules")],
        }
    )
    assert len(merged) == 1
    assert merged[0].source == "guardrail+grounding"
    assert merged[0].message == "from rules"  # the rule based message wins the tie


def test_findings_are_ordered_safety_first_then_by_section():
    merged = supervise(
        {
            "billing": [f("plan", "not_documented", "billing"), f("objective", "not_documented", "billing")],
            "grounding": [f("subjective", "ungrounded", "grounding")],
            "safety": [f("assessment", "safety_undocumented", "safety")],
        }
    )
    assert [(m.section, m.kind) for m in merged] == [
        ("assessment", "safety_undocumented"),
        ("subjective", "ungrounded"),
        ("objective", "not_documented"),
        ("plan", "not_documented"),
    ]


def test_dismissed_problems_stay_dismissed():
    merged = supervise(
        {"grounding": [f("plan", "ungrounded", "grounding"), f("objective", "ungrounded", "grounding")]},
        {("plan", "ungrounded")},
    )
    assert [(m.section, m.kind) for m in merged] == [("objective", "ungrounded")]


def test_no_reports_means_no_findings():
    assert supervise({}) == []


# --- each reviewer sticks to its own job ---------------------------------------


def test_a_reviewer_may_only_raise_its_own_kinds_and_is_tagged():
    reply = json.dumps(
        {
            "findings": [
                {"section": "assessment", "kind": "safety_undocumented", "message": "risk"},
                {"section": "plan", "kind": "not_documented", "message": "thin"},
            ]
        }
    )
    found = parse_role_findings(reply, "safety")
    assert [(x.kind, x.source) for x in found] == [("safety_undocumented", "safety")]


def test_every_role_has_a_prompt_that_names_its_kinds():
    from app.llm import ROLE_PROMPTS

    for role, (_, kinds) in ROLES.items():
        assert all(k in ROLE_PROMPTS[role] for k in kinds)


# --- the graph ------------------------------------------------------------------


class Recording(StubProvider):
    def __init__(self):
        self.roles = []
        self.threads = set()
        self._lock = threading.Lock()

    def review_as(self, role, raw_input, note):
        with self._lock:
            self.roles.append(role)
            self.threads.add(threading.get_ident())
        return super().review_as(role, raw_input, note)


def test_all_three_reviewers_run_each_round_and_the_supervisor_merges():
    provider = Recording()
    result = start_review(build_review_graph(provider), "t", RAW, NOTE)

    assert sorted(provider.roles) == ["billing", "grounding", "safety"]
    assert result.status == "paused"
    top = result.findings[0]
    assert (top["section"], top["kind"]) == ("assessment", "safety_undocumented")
    # The safety reviewer and the rule based check both raised it. One finding, both credited.
    assert top["source"] == "safety+guardrail"


def test_a_second_round_runs_the_reviewers_again():
    provider = Recording()
    graph = build_review_graph(provider)
    start_review(graph, "t2", RAW, NOTE)
    resume_review(graph, "t2", {"assessment": "dismiss"})
    assert sorted(provider.roles) == ["billing"] * 2 + ["grounding"] * 2 + ["safety"] * 2


def test_one_failing_specialist_fails_the_review_instead_of_skipping_it():
    class SafetyDown(StubProvider):
        def review_as(self, role, raw_input, note):
            if role == "safety":
                return "not json"
            return super().review_as(role, raw_input, note)

    with pytest.raises(LLMOutputError):
        start_review(build_review_graph(SafetyDown()), "t", RAW, NOTE)


def test_a_reviewer_cannot_get_a_finding_past_its_role():
    class Wanderer(StubProvider):
        def review_as(self, role, raw_input, note):
            # Every reviewer claims a safety problem. Only the safety reviewer is allowed to.
            item = {"section": "assessment", "kind": "safety_undocumented", "message": "x"}
            return json.dumps({"findings": [item]})

    clean_raw = "Client reports poor sleep and was tearful."
    result = start_review(build_review_graph(Wanderer()), "t", clean_raw, NOTE)
    assert [f["source"] for f in result.findings] == ["safety"]


def test_general_mode_runs_one_reviewer():
    provider = Recording()
    result = start_review(build_review_graph(provider, reviewers=("general",)), "t", RAW, NOTE)
    assert provider.roles == []  # review_as was never used
    assert result.findings  # the stub's general review plus the rules still found the risk


def test_unknown_reviewers_are_rejected_when_the_graph_is_built():
    with pytest.raises(ValueError, match="Unknown reviewers"):
        build_review_graph(StubProvider(), reviewers=("grounding", "billng"))
    with pytest.raises(ValueError):
        build_review_graph(StubProvider(), reviewers=())


def test_near_miss_section_and_kind_wording_is_accepted():
    reply = (
        '{"findings": [{"section": "Plan", "kind": "Not documented", "message": "No plan."},'
        '{"section": "A", "kind": "other", "message": "Thin."}]}'
    )
    found = parse_role_findings(reply, "billing")
    assert [(f.section, f.kind, f.source) for f in found] == [
        ("plan", "not_documented", "billing"),
        ("assessment", "other", "billing"),
    ]


def test_an_unusable_finding_is_skipped_but_good_ones_stay():
    reply = (
        '{"findings": [{"section": "overall", "kind": "other", "message": "x"},'
        '{"section": "plan", "kind": "other", "message": "No follow up."}]}'
    )
    assert len(parse_role_findings(reply, "billing")) == 1


def test_a_reply_with_only_unusable_findings_is_rejected():
    import pytest

    reply = '{"findings": [{"section": "overall", "kind": "nonsense", "message": "x"}]}'
    with pytest.raises(ValueError):
        parse_role_findings(reply, "billing")
