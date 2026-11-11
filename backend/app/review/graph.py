"""Review before signing, as a LangGraph workflow.

The flow:

    grounding_review --+
    safety_review    --+--> supervise -> (no findings)   -> mark_ready          -> END
    billing_review   --+              -> (out of rounds) -> mark_needs_attention -> END
                                      -> (findings)      -> propose_fixes -> wait_for_therapist
                                                                              -> the three reviewers

The three reviewers run in parallel. Each one has its own prompt and may only raise its
own kinds of finding. The supervisor merges their findings with the rule based
guardrails, removes duplicates, orders them with safety first, and drops problems the
therapist already dismissed. Passing reviewers=("general",) runs one all purpose
reviewer instead, which is how the evals compare one reviewer with three.

The graph pauses in wait_for_therapist until a person answers. It never edits the
saved note. The caller applies the result, so a human always has the last word.

Findings carry a `source`: the reviewer that raised them, or several joined with "+".
"""

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .. import metrics
from ..config import get_settings
from ..guardrails import check_note as guardrail_check
from ..guardrails import merge
from ..llm import ROLES, LLMProvider, parse_findings, parse_fixes, parse_role_findings
from ..schemas import SECTIONS, Finding
from ..services.notes import _generate
from .supervisor import supervise

Decision = Literal["accept", "dismiss"]


class ReviewState(TypedDict, total=False):
    raw_input: str
    note: dict[str, str]  # the text under review, updated as fixes are accepted
    grounding_findings: list[dict]  # one key per reviewer, so parallel nodes never collide
    safety_findings: list[dict]
    billing_findings: list[dict]
    general_findings: list[dict]
    findings: list[dict]  # what the supervisor kept
    fixes: dict[str, str]  # proposed replacement text, keyed by section
    dismissed: list[list[str]]  # [section, kind] pairs the therapist chose to keep
    round: int
    max_rounds: int
    status: str  # running, ready, or needs_attention


@dataclass
class ReviewResult:
    # "paused" means a person must answer. The others are final.
    status: Literal["paused", "ready", "needs_attention"]
    round: int
    note: dict[str, str]
    findings: list[dict] = field(default_factory=list)
    fixes: dict[str, str] = field(default_factory=dict)


def build_review_graph(
    provider: LLMProvider,
    checkpointer: BaseCheckpointSaver | None = None,
    reviewers: tuple[str, ...] | None = None,
):
    """Compile the graph. A checkpointer is required, because the graph pauses."""
    if reviewers is None:
        reviewers = tuple(
            r.strip() for r in get_settings().review_reviewers.split(",") if r.strip()
        )
    unknown = [r for r in reviewers if r != "general" and r not in ROLES]
    if not reviewers or unknown:
        raise ValueError(f"Unknown reviewers {unknown or reviewers}. Use {list(ROLES)} or 'general'.")

    def make_reviewer(role: str):
        def run(state: ReviewState) -> dict:
            if role == "general":
                found, _ = _generate(
                    lambda: provider.review_note(state["raw_input"], state["note"]),
                    parse_findings,
                    "review",
                )
            else:
                found, _ = _generate(
                    lambda: provider.review_as(role, state["raw_input"], state["note"]),
                    lambda text: parse_role_findings(text, role),
                    f"{role} review",
                )
            return {f"{role}_findings": [f.model_dump() for f in found]}

        return run

    def supervise_round(state: ReviewState) -> dict:
        reports = {
            role: [Finding(**f) for f in state.get(f"{role}_findings", [])]
            for role in reviewers
        }
        # Rule based checks run on every model, so a weak reviewer cannot miss them.
        reports["guardrail"] = guardrail_check(state["raw_input"], state["note"])
        for f in reports["guardrail"]:
            metrics.record_guardrail(f.kind)
        skip = {tuple(pair) for pair in state.get("dismissed", [])}
        kept = supervise(reports, skip)
        return {"findings": [f.model_dump() for f in kept], "fixes": {}}

    def route_after_check(state: ReviewState) -> str:
        if not state["findings"]:
            return "mark_ready"
        if state["round"] >= state["max_rounds"]:
            return "mark_needs_attention"
        return "propose_fixes"

    def propose_fixes(state: ReviewState) -> dict:
        flagged = {f["section"] for f in state["findings"]}
        fixes, _ = _generate(
            lambda: provider.propose_fixes(
                state["raw_input"], state["note"], state["findings"]
            ),
            lambda text: parse_fixes(text, flagged),
            "set of fixes",
        )
        return {"fixes": fixes}

    def wait_for_therapist(state: ReviewState) -> dict:
        # interrupt() stops the run here. On resume this node runs again from the
        # top and interrupt() returns the answer, so nothing before it may have
        # side effects.
        answer = interrupt({"round": state["round"], "findings": state["findings"]})
        decisions: dict[str, str] = answer.get("decisions", {})
        edits: dict[str, str] = answer.get("edits", {})

        note = dict(state["note"])
        dismissed = [list(pair) for pair in state.get("dismissed", [])]
        for section, text in edits.items():
            note[section] = text
        for section, decision in decisions.items():
            if decision == "accept" and section in state["fixes"] and section not in edits:
                note[section] = state["fixes"][section]
            elif decision == "dismiss":
                for f in state["findings"]:
                    pair = [f["section"], f["kind"]]
                    if f["section"] == section and pair not in dismissed:
                        dismissed.append(pair)
        return {
            "note": note,
            "dismissed": dismissed,
            "fixes": {},
            "round": state["round"] + 1,
        }

    graph = StateGraph(ReviewState)
    for role in reviewers:
        graph.add_node(f"{role}_review", make_reviewer(role))
    graph.add_node("supervise", supervise_round)
    graph.add_node("propose_fixes", propose_fixes)
    graph.add_node("wait_for_therapist", wait_for_therapist)
    graph.add_node("mark_ready", lambda state: {"status": "ready"})
    graph.add_node("mark_needs_attention", lambda state: {"status": "needs_attention"})

    for role in reviewers:
        graph.add_edge(START, f"{role}_review")
        graph.add_edge("wait_for_therapist", f"{role}_review")
    # Waits for every reviewer, then runs once.
    graph.add_edge([f"{role}_review" for role in reviewers], "supervise")
    graph.add_conditional_edges(
        "supervise",
        route_after_check,
        ["mark_ready", "mark_needs_attention", "propose_fixes"],
    )
    graph.add_edge("propose_fixes", "wait_for_therapist")
    graph.add_edge("mark_ready", END)
    graph.add_edge("mark_needs_attention", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


def _result(graph, config) -> ReviewResult:
    snapshot = graph.get_state(config)
    values = snapshot.values
    status = "paused" if snapshot.next else values["status"]
    return ReviewResult(
        status=status,
        round=values["round"],
        note=values["note"],
        findings=values.get("findings", []),
        fixes=values.get("fixes", {}),
    )


def start_review(
    graph, thread_id: str, raw_input: str, note: dict[str, str], max_rounds: int | None = None
) -> ReviewResult:
    """Run the first check. Returns paused, ready, or needs_attention."""
    config = {"configurable": {"thread_id": thread_id}}
    graph.invoke(
        {
            "raw_input": raw_input,
            "note": dict(note),
            "dismissed": [],
            "round": 0,
            "max_rounds": max_rounds or get_settings().review_max_rounds,
            "status": "running",
        },
        config,
    )
    return _result(graph, config)


def resume_review(
    graph,
    thread_id: str,
    decisions: dict[str, Decision],
    edits: dict[str, str] | None = None,
) -> ReviewResult:
    """Send the therapist's answers into a paused review and run until the next stop.

    decisions maps a section to "accept" (use the proposed fix) or "dismiss" (keep
    the text as written and stop flagging that problem). edits maps a section to
    text the therapist typed, and wins over a proposed fix.
    """
    edits = edits or {}
    for section in {*decisions, *edits}:
        if section not in SECTIONS:
            raise ValueError(f"Unknown section: {section}")
    for decision in decisions.values():
        if decision not in ("accept", "dismiss"):
            raise ValueError(f"Unknown decision: {decision}")
    if any(not text.strip() for text in edits.values()):
        raise ValueError("An edit cannot be empty")

    config = {"configurable": {"thread_id": thread_id}}
    if not graph.get_state(config).next:
        raise ValueError("This review is not waiting for an answer")
    graph.invoke(Command(resume={"decisions": decisions, "edits": edits}), config)
    return _result(graph, config)


def review_status(graph, thread_id: str) -> ReviewResult | None:
    """Current state of a review, or None if no review exists for this thread."""
    config = {"configurable": {"thread_id": thread_id}}
    if not graph.get_state(config).values:
        return None
    return _result(graph, config)
