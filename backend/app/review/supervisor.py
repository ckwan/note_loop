"""The supervisor: merges what the reviewers found into one ranked list.

It is plain code and not a model call, so the same findings always give the same
result and the merge rules are easy to test.
"""

from ..schemas import SECTIONS, Finding

# Most urgent first.
KIND_ORDER = ["safety_undocumented", "ungrounded", "inconsistent", "not_documented", "other"]
# When two reviewers raise the same problem, the first one here supplies the message.
SOURCE_ORDER = ["safety", "guardrail", "grounding", "billing", "general"]


def _rank(source: str) -> int:
    return SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER)


def supervise(
    reports: dict[str, list[Finding]], dismissed: set[tuple[str, str]] | None = None
) -> list[Finding]:
    """Combine reviewer reports.

    * The same section and kind from several reviewers becomes one finding that lists
      every source, such as "safety+guardrail".
    * A problem the therapist already dismissed stays dismissed.
    * The result is ordered by urgency, with safety first, then by note section.
    """
    dismissed = dismissed or set()
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for findings in reports.values():
        for f in findings:
            if (f.section, f.kind) not in dismissed:
                grouped.setdefault((f.section, f.kind), []).append(f)

    merged = []
    for (section, kind), items in grouped.items():
        items.sort(key=lambda f: _rank(f.source))
        sources = sorted({f.source for f in items}, key=_rank)
        merged.append(
            Finding(section=section, kind=kind, message=items[0].message, source="+".join(sources))
        )
    merged.sort(key=lambda f: (KIND_ORDER.index(f.kind), SECTIONS.index(f.section)))
    return merged
