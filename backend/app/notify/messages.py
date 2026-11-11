"""Builders for each message. Pure functions, so they are safe inside workflows.

Every value here is an id, a status, or a count. Add nothing that came from a note.
"""

from app.notify.types import Notification


def claim_failed(note_id: int) -> Notification:
    return Notification(
        title="Claim failed after retries",
        body=(
            "Every attempt to submit this claim was used up. It is marked failed. "
            "It will be picked up by the next batch resubmit."
        ),
        level="error",
        fields={"Note": str(note_id)},
    )


def claim_denied(note_id: int, claim_id: int) -> Notification:
    return Notification(
        title="Claim denied",
        body="The adjudication service denied this claim.",
        level="warning",
        fields={"Note": str(note_id), "Claim": str(claim_id), "Status": "denied"},
    )


def claim_needs_follow_up(note_id: int, claim_id: int) -> Notification:
    return Notification(
        title="Claim needs manual follow up",
        body="No final decision arrived in time. A person needs to check this claim.",
        level="warning",
        fields={"Note": str(note_id), "Claim": str(claim_id), "Status": "manual_follow_up"},
    )


def batch_started(chunk_size: int) -> Notification:
    return Notification(
        title="Resubmit batch started",
        body="Retrying failed claims, one chunk at a time.",
        level="info",
        fields={"Chunk size": str(chunk_size)},
    )


def batch_finished(processed: int, succeeded: int, failed: int) -> Notification:
    if processed == 0:
        return Notification(
            title="Resubmit batch finished",
            body="There were no failed claims to retry.",
            level="info",
            fields={"Processed": "0"},
        )
    return Notification(
        title="Resubmit batch finished",
        body=(
            "Every claim went through."
            if failed == 0
            else "Some claims failed again and need a person to look at them."
        ),
        level="success" if failed == 0 else "warning",
        fields={
            "Processed": str(processed),
            "Succeeded": str(succeeded),
            "Failed again": str(failed),
        },
    )


def review_needs_attention(session_id: int, round_number: int, findings: list[tuple[str, str]]) -> Notification:
    # Sections and finding types only. The finding text can quote the note.
    open_items = ", ".join(f"{section}: {kind}" for section, kind in findings) or "none"
    return Notification(
        title="Review finished with open findings",
        body="The review used all its rounds and some problems are still open.",
        level="warning",
        fields={
            "Session": str(session_id),
            "Rounds": str(round_number),
            "Open findings": open_items,
        },
    )


def daily_summary(counts: dict[str, int]) -> Notification:
    failed = counts.get("failed", 0)
    return Notification(
        title="Daily claims summary",
        body=(
            "No failed claims."
            if failed == 0
            else f"{failed} failed claim(s) are waiting for a resubmit."
        ),
        level="success" if failed == 0 else "warning",
        fields={
            "Pending": str(counts.get("pending", 0)),
            "Created": str(counts.get("created", 0)),
            "Submitted": str(counts.get("submitted", 0)),
            "Failed": str(failed),
        },
    )


def llm_budget_exceeded(
    tokens: int, cost_usd: float, cost_budget: float, token_budget: int
) -> Notification:
    fields = {"Tokens today": f"{tokens:,}", "Estimated cost today": f"${cost_usd:.2f}"}
    if cost_budget:
        fields["Cost budget"] = f"${cost_budget:.2f}"
    if token_budget:
        fields["Token budget"] = f"{token_budget:,}"
    return Notification(
        title="Model spend passed the daily budget",
        body="Calls still work. Check what is driving the usage before it grows.",
        level="warning",
        fields=fields,
    )


def llm_failures(count: int, provider: str, model: str) -> Notification:
    return Notification(
        title="Model calls are failing",
        body="Drafts and reviews will return errors until the model answers again.",
        level="error",
        fields={"Failed in a row": str(count), "Provider": provider, "Model": model},
    )


def llm_slow(count: int, slowest_seconds: float, threshold: float, purpose: str, model: str) -> Notification:
    return Notification(
        title="Slow model calls",
        body="Some calls took longer than the limit. Users are waiting on these.",
        level="warning",
        fields={
            "Slow calls": str(count),
            "Slowest": f"{slowest_seconds:.1f}s",
            "Limit": f"{threshold:.1f}s",
            "Call": purpose,
            "Model": model,
        },
    )
