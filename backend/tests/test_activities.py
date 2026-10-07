import dataclasses

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditEvent, Claim
from app.workflows.activities import mark_claim_failed, prepare_claim, submit_claim

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
