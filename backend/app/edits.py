"""How much a therapist changed the AI draft.

The measure is the share of words that were added, removed or replaced, compared with
the longer of the two texts. 0.0 means the draft was signed as written. 1.0 means none
of it survived. It is computed from the saved ai_draft and final_note, so it needs no
extra storage, and it returns numbers only, never note text.
"""

from difflib import SequenceMatcher
from statistics import mean, median

from .schemas import SECTIONS


def _counts(before: str, after: str) -> tuple[int, int]:
    """Words changed and the size they are measured against."""
    a, b = before.split(), after.split()
    size = max(len(a), len(b))
    if size == 0:
        return 0, 0
    changed = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            changed += max(i2 - i1, j2 - j1)
    return changed, size


def edit_rate(before: str, after: str) -> float:
    changed, size = _counts(before, after)
    return round(changed / size, 3) if size else 0.0


def edit_stats(draft: dict, final: dict) -> dict:
    """Per section rates plus one overall rate weighted by how long each section is."""
    per_section: dict[str, float] = {}
    changed_total = size_total = 0
    for name in SECTIONS:
        changed, size = _counts(draft[name], final[name])
        per_section[name] = round(changed / size, 3) if size else 0.0
        changed_total += changed
        size_total += size
    overall = round(changed_total / size_total, 3) if size_total else 0.0
    return {"edit_rate": overall, "section_edit_rates": per_section}


def summarize(rows: list[tuple[dict, dict]]) -> dict:
    """Roll up many signed notes, given (ai_draft, final_note) pairs."""
    stats = [edit_stats(d, f) for d, f in rows]
    if not stats:
        return {
            "signed_notes": 0,
            "mean_edit_rate": 0.0,
            "median_edit_rate": 0.0,
            "signed_unchanged_share": 0.0,
            "by_section": {name: 0.0 for name in SECTIONS},
        }
    rates = [s["edit_rate"] for s in stats]
    return {
        "signed_notes": len(stats),
        "mean_edit_rate": round(mean(rates), 3),
        "median_edit_rate": round(median(rates), 3),
        "signed_unchanged_share": round(sum(1 for r in rates if r == 0) / len(rates), 3),
        "by_section": {
            name: round(mean(s["section_edit_rates"][name] for s in stats), 3)
            for name in SECTIONS
        },
    }
