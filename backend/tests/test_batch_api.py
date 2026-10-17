from app.config import get_settings
from app.demo_batch import create_failed_claims
from app.errors import ConflictError
from app.main import app
from app.routes import get_workflow_starter
from app.workflows.starter import NoopStarter


def test_summary_counts_claims_by_status(client):
    assert client.get("/claims/summary").json() == {
        "pending": 0,
        "created": 0,
        "submitted": 0,
        "failed": 0,
    }
    create_failed_claims(4)
    assert client.get("/claims/summary").json()["failed"] == 4


def test_resubmit_starts_the_batch_with_the_default_chunk_size(client, starter):
    res = client.post("/claims/resubmit-failed")
    assert res.status_code == 202
    assert res.json() == {
        "workflow_id": "resubmit-failed-claims",
        "chunk_size": get_settings().resubmit_chunk_size,
    }
    assert starter.batches == [get_settings().resubmit_chunk_size]


def test_resubmit_accepts_a_custom_chunk_size(client, starter):
    res = client.post("/claims/resubmit-failed", json={"chunk_size": 10})
    assert res.status_code == 202
    assert starter.batches == [10]


def test_resubmit_rejects_a_bad_chunk_size(client, starter):
    assert client.post("/claims/resubmit-failed", json={"chunk_size": 0}).status_code == 422
    assert client.post("/claims/resubmit-failed", json={"chunk_size": 9999}).status_code == 422
    assert starter.batches == []


def test_resubmit_returns_409_when_a_batch_is_already_running(client):
    class BusyStarter:
        async def start_resubmit_failed(self, chunk_size):
            raise ConflictError("A resubmit batch is already running")

    app.dependency_overrides[get_workflow_starter] = lambda: BusyStarter()
    res = client.post("/claims/resubmit-failed")
    assert res.status_code == 409


def test_resubmit_returns_503_when_temporal_is_turned_off(client):
    app.dependency_overrides[get_workflow_starter] = lambda: NoopStarter()
    res = client.post("/claims/resubmit-failed")
    assert res.status_code == 503
