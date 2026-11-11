import dataclasses

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEvent, Claim
from app.demo_batch import create_failed_claims
from app.workflows.activities import (
    fetch_failed_claims,
    mark_claim_failed,
    prepare_claim,
    reset_failed_claim,
    submit_claim,
)

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."


def signed_note_id(client) -> int:
    client.post("/sessions/1/draft", json={"raw_input": RAW})
    client.post("/sessions/1/note/sign")
    return client.get("/sessions/1/note").json()["id"]


def audit_actions(note_id: int) -> list[str]:
    with SessionLocal() as db:
        rows = db.query(AuditEvent).filter_by(note_id=note_id).order_by(AuditEvent.id)
        return [r.action for r in rows]


def claim_for(note_id: int) -> Claim:
    with SessionLocal() as db:
        return db.query(Claim).filter_by(note_id=note_id).one()


def test_prepare_claim_moves_pending_to_created_once(client):
    note_id = signed_note_id(client)
    env = ActivityEnvironment()

    first = env.run(prepare_claim, note_id)
    second = env.run(prepare_claim, note_id)

    assert first == second
    assert claim_for(note_id).status == "created"
    assert audit_actions(note_id).count("claim_created") == 1


def test_submit_claim_sets_reference_and_is_idempotent(client):
    note_id = signed_note_id(client)
    env = ActivityEnvironment()
    claim_id = env.run(prepare_claim, note_id)

    ref_one = env.run(submit_claim, claim_id)
    ref_two = env.run(submit_claim, claim_id)

    assert ref_one == ref_two == f"SIM-{claim_id:06d}"
    claim = claim_for(note_id)
    assert claim.status == "submitted"
    assert claim.submitted_at is not None
    assert audit_actions(note_id).count("claim_submitted") == 1


def test_simulated_payer_failures_clear_on_a_later_attempt(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "simulate_payer_failures", 2)
    note_id = signed_note_id(client)
    claim_id = ActivityEnvironment().run(prepare_claim, note_id)

    env = ActivityEnvironment()
    with pytest.raises(RuntimeError):
        env.run(submit_claim, claim_id)

    env.info = dataclasses.replace(env.info, attempt=3)
    assert env.run(submit_claim, claim_id).startswith("SIM-")


def test_prepare_claim_rejects_an_unsigned_note_without_retrying(client):
    client.post("/sessions/1/draft", json={"raw_input": RAW})
    note_id = client.get("/sessions/1/note").json()["id"]

    with pytest.raises(ApplicationError) as excinfo:
        ActivityEnvironment().run(prepare_claim, note_id)
    assert excinfo.value.non_retryable is True


def test_mark_claim_failed_records_the_failure(client):
    note_id = signed_note_id(client)
    ActivityEnvironment().run(mark_claim_failed, note_id)

    assert claim_for(note_id).status == "failed"
    assert "claim_failed" in audit_actions(note_id)


def test_fetch_failed_claims_pages_by_id_in_order(client):
    create_failed_claims(5)
    env = ActivityEnvironment()

    first = env.run(fetch_failed_claims, 0, 2)
    assert len(first) == 2
    assert first[0].claim_id < first[1].claim_id

    rest = env.run(fetch_failed_claims, first[-1].claim_id, 10)
    assert len(rest) == 3
    assert rest[0].claim_id > first[-1].claim_id
    assert env.run(fetch_failed_claims, rest[-1].claim_id, 10) == []


def test_fetch_failed_claims_skips_claims_that_are_not_failed(client):
    note_id = signed_note_id(client)  # a pending claim, not a failed one
    create_failed_claims(2)

    refs = ActivityEnvironment().run(fetch_failed_claims, 0, 10)
    assert len(refs) == 2
    assert note_id not in [r.note_id for r in refs]


def test_reset_failed_claim_is_repeatable(client):
    create_failed_claims(1)
    ref = ActivityEnvironment().run(fetch_failed_claims, 0, 1)[0]
    env = ActivityEnvironment()

    env.run(reset_failed_claim, ref.claim_id)
    env.run(reset_failed_claim, ref.claim_id)

    assert claim_for(ref.note_id).status == "created"
    assert audit_actions(ref.note_id).count("claim_resubmit_requested") == 1


def test_reset_leaves_a_submitted_claim_alone(client):
    note_id = signed_note_id(client)
    env = ActivityEnvironment()
    claim_id = env.run(prepare_claim, note_id)
    env.run(submit_claim, claim_id)

    env.run(reset_failed_claim, claim_id)

    assert claim_for(note_id).status == "submitted"
    assert "claim_resubmit_requested" not in audit_actions(note_id)


class _Recorder:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, notification):
        if self.fail:
            raise RuntimeError("slack is down")
        self.sent.append(notification)


def test_notify_slack_sends_and_links_to_the_workflow(client, monkeypatch):
    from app.notify import messages
    from app.workflows.activities import notify_slack

    rec = _Recorder()
    monkeypatch.setattr("app.workflows.activities.build_notifier", lambda settings: rec)

    ActivityEnvironment().run(notify_slack, messages.claim_failed(4), "note-signed-4")

    (sent,) = rec.sent
    assert sent.link == "http://localhost:8233/namespaces/default/workflows/note-signed-4"
    assert sent.link_text == "Open in Temporal"


def test_notify_slack_raises_so_temporal_can_retry(client, monkeypatch):
    from app.notify import messages
    from app.workflows.activities import notify_slack

    monkeypatch.setattr(
        "app.workflows.activities.build_notifier", lambda settings: _Recorder(fail=True)
    )
    with pytest.raises(RuntimeError):
        ActivityEnvironment().run(notify_slack, messages.claim_failed(4), None)


def test_notify_slack_keeps_a_link_that_is_already_set(client, monkeypatch):
    from app.notify.types import Notification
    from app.workflows.activities import notify_slack

    rec = _Recorder()
    monkeypatch.setattr("app.workflows.activities.build_notifier", lambda settings: rec)

    ActivityEnvironment().run(
        notify_slack, Notification(title="t", link="https://example.com/x"), "wf-1"
    )
    assert rec.sent[0].link == "https://example.com/x"


def test_get_claim_counts_reports_each_status(client):
    from app.workflows.activities import get_claim_counts

    create_failed_claims(3)
    signed_note_id(client)  # one more claim, still pending

    counts = ActivityEnvironment().run(get_claim_counts)
    assert counts["failed"] == 3
    assert counts["pending"] == 1
    assert counts["submitted"] == 0
