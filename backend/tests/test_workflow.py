"""End to end workflow tests.

These need a Temporal server. By default they use the Temporal test server, which
the SDK downloads on first use. If that download is blocked (offline machine,
locked down CI), the tests skip.

To run them against a local Temporal CLI instead, set TEMPORAL_DEV_SERVER_PATH to
the `temporal` binary. That server does not skip time, so retry backoff runs in real
time and the failure tests take about 15 seconds each.
"""

import asyncio
import contextlib
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment

from app.config import get_settings
from app.db import SessionLocal
from app.demo_batch import create_failed_claims
from app.models import Claim
from app.worker import build_workers
from app.workflows.types import BATCH_WORKFLOW_ID, BatchParams, BatchResult
from app.workflows.workflows import NoteSignedWorkflow, ResubmitFailedClaimsWorkflow

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."


def signed_note_id(client) -> int:
    client.post("/sessions/1/draft", json={"raw_input": RAW})
    client.post("/sessions/1/note/sign")
    return client.get("/sessions/1/note").json()["id"]


async def _start_env() -> WorkflowEnvironment:
    path = os.environ.get("TEMPORAL_DEV_SERVER_PATH")
    try:
        if path:
            return await WorkflowEnvironment.start_local(dev_server_existing_path=path)
        return await WorkflowEnvironment.start_time_skipping()
    except RuntimeError as exc:
        pytest.skip(f"Temporal test server unavailable: {exc}")


def run_workflow(workflow_run, arg, workflow_id: str):
    """Run one workflow to completion with both workers, and return its result."""

    async def go():
        env = await _start_env()
        async with env:
            workers = build_workers(env.client, "test", 0, ThreadPoolExecutor(max_workers=8))
            async with contextlib.AsyncExitStack() as stack:
                for worker in workers:
                    await stack.enter_async_context(worker)
                return await env.client.execute_workflow(
                    workflow_run, arg, id=workflow_id, task_queue="test"
                )

    return asyncio.run(go())


def claim_statuses() -> list[str]:
    with SessionLocal() as db:
        return [c.status for c in db.query(Claim).order_by(Claim.id)]


def test_workflow_submits_the_claim(client):
    note_id = signed_note_id(client)
    reference = run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")

    with SessionLocal() as db:
        claim = db.query(Claim).filter_by(note_id=note_id).one()
    assert claim.status == "submitted"
    assert reference == claim.payer_reference


def test_workflow_retries_through_payer_failures(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "simulate_payer_failures", 2)
    note_id = signed_note_id(client)
    run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")

    assert claim_statuses() == ["submitted"]


def test_workflow_marks_the_claim_failed_when_retries_run_out(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "simulate_payer_failures", 99)
    note_id = signed_note_id(client)

    with pytest.raises(WorkflowFailureError):
        run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")

    assert claim_statuses() == ["failed"]


def test_batch_resubmits_every_failed_claim_in_chunks(client):
    create_failed_claims(7)

    result = run_workflow(
        ResubmitFailedClaimsWorkflow.run, BatchParams(chunk_size=3), BATCH_WORKFLOW_ID
    )

    assert result == BatchResult(processed=7, succeeded=7, failed=0)
    assert claim_statuses() == ["submitted"] * 7


def test_batch_with_nothing_to_do_returns_zeros(client):
    result = run_workflow(
        ResubmitFailedClaimsWorkflow.run, BatchParams(chunk_size=5), BATCH_WORKFLOW_ID
    )
    assert result == BatchResult(processed=0, succeeded=0, failed=0)


def test_batch_leaves_claims_that_are_not_failed_alone(client):
    create_failed_claims(2)
    with SessionLocal() as db:
        first = db.query(Claim).order_by(Claim.id).first()
        first.status = "submitted"
        first.payer_reference = "SIM-EXISTING"
        db.commit()

    result = run_workflow(
        ResubmitFailedClaimsWorkflow.run, BatchParams(chunk_size=5), BATCH_WORKFLOW_ID
    )

    assert result.processed == 1
    with SessionLocal() as db:
        assert db.query(Claim).order_by(Claim.id).first().payer_reference == "SIM-EXISTING"


def test_batch_counts_claims_that_fail_again_and_does_not_loop(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "simulate_payer_failures", 99)
    create_failed_claims(3)

    result = run_workflow(
        ResubmitFailedClaimsWorkflow.run, BatchParams(chunk_size=2), BATCH_WORKFLOW_ID
    )

    # Each claim is tried once. The ones that fail again are not picked up a second
    # time, because the cursor has already moved past them.
    assert result == BatchResult(processed=3, succeeded=0, failed=3)
    assert claim_statuses() == ["failed"] * 3
