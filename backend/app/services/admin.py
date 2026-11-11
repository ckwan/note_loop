"""Numbers for the admin page, worked out from data the app already keeps.

Review activity comes from the audit trail, so nothing new is stored. Only counts and
labels are read. Finding text and note text never leave this module.
"""

import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import metrics
from ..models import AuditEvent, Note
from ..schemas import SECTIONS
from . import notes as notes_svc

REVIEW_ACTIONS = {"review_started", "review_answered"}
RESULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "results"


def _blank() -> dict:
    return {"raised": 0, "shared": 0, "accepted": 0, "dismissed": 0, "rewritten": 0}


def review_activity(db: Session) -> dict:
    events = db.execute(
        select(AuditEvent)
        .where(AuditEvent.action.in_(REVIEW_ACTIONS | {"signed", "draft_generated"}))
        .order_by(AuditEvent.note_id, AuditEvent.id)
    ).scalars()

    by_source: dict[str, dict] = defaultdict(_blank)
    by_kind: dict[str, int] = defaultdict(int)
    reviews_started = rounds = merged_away = 0
    signed = signed_unreviewed = signed_unresolved = 0
    recent: list[dict] = []

    per_note: dict[int, list[AuditEvent]] = defaultdict(list)
    for e in events:
        per_note[e.note_id].append(e)

    for note_id, seq in per_note.items():
        last_findings: list[dict] = []
        last_status: str | None = None
        for e in seq:
            d = e.detail or {}
            if e.action == "review_started":
                reviews_started += 1
            if e.action in REVIEW_ACTIONS:
                rounds += 1
                last_findings = d.get("findings", [])
                last_status = d.get("status")
                if e.action == "review_started":
                    recent.append(
                        {
                            "note_id": note_id,
                            "at": e.created_at.isoformat(),
                            "status": d.get("status"),
                            "findings": len(last_findings),
                            "sources": sorted(
                                {s for f in last_findings for s in f["source"].split("+")}
                            ),
                        }
                    )
                for f in last_findings:
                    sources = f["source"].split("+")
                    by_kind[f["kind"]] += 1
                    merged_away += len(sources) - 1
                    for s in sources:
                        row = by_source[s]
                        row["raised"] += 1
                        if len(sources) > 1:
                            row["shared"] += 1
            if e.action == "review_answered":
                # Decisions are per section, so credit every finding in that section
                # from the review the therapist was looking at.
                prior = [x for x in seq if x.id < e.id and x.action in REVIEW_ACTIONS]
                shown = (prior[-1].detail or {}).get("findings", []) if prior else []
                for outcome, key in (("accepted", "accepted"), ("dismissed", "dismissed"), ("edited", "rewritten")):
                    for section in d.get(outcome, []):
                        for f in shown:
                            if f["section"] == section:
                                for s in f["source"].split("+"):
                                    by_source[s][key] += 1
            if e.action == "signed":
                signed += 1
                reviewed = any(x.action == "review_started" for x in seq if x.id < e.id)
                if not reviewed:
                    signed_unreviewed += 1
                elif last_status != "ready":
                    signed_unresolved += 1

    recent.sort(key=lambda r: r["at"], reverse=True)
    return {
        "reviews_started": reviews_started,
        "review_rounds": rounds,
        "findings_merged_away": merged_away,
        "signed_notes": signed,
        "signed_without_review": signed_unreviewed,
        "signed_with_open_findings": signed_unresolved,
        "by_reviewer": dict(sorted(by_source.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "recent": recent[:15],
    }


def llm_activity() -> list[dict]:
    """Model calls since the API started, grouped by what the call was for."""
    records = metrics.calls_since(0)
    groups: dict[str, list] = defaultdict(list)
    for r in records:
        groups[r.purpose].append(r)
    rows = []
    for purpose, items in sorted(groups.items()):
        s = metrics.summarize_calls(items)
        rows.append({"purpose": purpose, **{k: round(v, 3) if isinstance(v, float) else v for k, v in s.items()}})
    return rows


def eval_results() -> list[dict]:
    """The newest saved eval run for each model."""
    if not RESULTS_DIR.is_dir():
        return []
    newest: dict[tuple[str, str], dict] = {}
    for path in sorted(RESULTS_DIR.glob("*.json")):
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict) or "summary" not in row:
            continue
        newest[(row.get("provider", ""), row.get("model", ""))] = {
            "provider": row.get("provider"),
            "model": row.get("model"),
            "date": row.get("date"),
            "summary": row["summary"],
            "usage": row.get("usage", {}),
        }
    return sorted(newest.values(), key=lambda r: r["date"] or "", reverse=True)


def overview(db: Session) -> dict:
    return {
        "review": review_activity(db),
        "edits": notes_svc.edit_summary(db),
        "claims": notes_svc.claim_summary(db).model_dump(),
        "llm": llm_activity(),
        "evals": eval_results(),
        "sections": list(SECTIONS),
    }
