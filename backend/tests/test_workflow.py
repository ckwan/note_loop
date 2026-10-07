"""End to end workflow tests.

These need the Temporal test server, which the Temporal SDK downloads on first
use. If the download is blocked (offline machine, locked down CI), the tests skip.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.config import get_settings
from app.db import SessionLocal
from app.models import Claim
from app.workflows.activities import mark_claim_failed, prepare_claim, submit_claim
from app.workflows.workflows import NoteSignedWorkflow

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."


def signed_note_id(client) -> int:
    client.post("/sessions/1/draft", json={"raw_input": RAW})
    client.post("/sessions/1/note/sign")
    return client.get("/sessions/1/note").json()["id"]


def run_workflow(note_id: int):
    async def go():
        try:
            env = await WorkflowEnvironment.start_time_skipping()
        except RuntimeError as exc:
            pytest.skip(f"Temporal test server unavailable: {exc}")
        async with env:
            async with Worker(
                env.client,
                task_queue="test",
                workflows=[NoteSignedWorkflow],
                activities=[prepare_claim, submit_claim, mark_claim_failed],
                activity_executor=ThreadPoolExecutor(max_workers=2),
            ):
                return await env.client.execute_workflow(
                    NoteSignedWorkflow.run,
                    note_id,
                    id=f"test-{note_id}",
                    task_queue="test",
                )

    return asyncio.run(go())


def test_workflow_submits_the_claim(client):
    note_id = signed_note_id(client)
    reference = run_workflow(note_id)

    with SessionLocal() as db:
        claim = db.query(Claim).filter_by(note_id=note_id).one()
    assert claim.status == "submitted"
    assert reference == claim.payer_reference


def test_workflow_retries_through_payer_failures(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "simulate_payer_failures", 2)
    note_id = signed_note_id(client)
    run_workflow(note_id)

    with SessionLocal() as db:
        assert db.query(Claim).filter_by(note_id=note_id).one().status == "submitted"


def test_workflow_marks_the_claim_failed_when_retries_run_out(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "simulate_payer_failures", 99)
    note_id = signed_note_id(client)

    with pytest.raises(WorkflowFailureError):
        run_workflow(note_id)

    with SessionLocal() as db:
        assert db.query(Claim).filter_by(note_id=note_id).one().status == "failed"
