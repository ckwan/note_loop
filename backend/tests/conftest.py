import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["LLM_PROVIDER"] = "stub"
os.environ["TEMPORAL_ENABLED"] = "false"

import fakeredis
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from app.db import Base, SessionLocal, engine
from app.main import app
from app.redis_client import get_redis_client
from app.review.checkpointer import get_checkpointer
from app.routes import get_notifier, get_workflow_starter
from app.seed import seed


class RecordingStarter:
    """Stands in for Temporal. Records which notes would have started a workflow."""

    def __init__(self) -> None:
        self.started: list[int] = []
        self.batches: list[int] = []

    async def start_note_signed(self, note_id: int) -> None:
        self.started.append(note_id)

    async def start_resubmit_failed(self, chunk_size: int) -> str:
        self.batches.append(chunk_size)
        return "resubmit-failed-claims"


class RecordingNotifier:
    """Stands in for Slack. Keeps every message that would have been sent."""

    def __init__(self) -> None:
        self.sent: list = []

    def send(self, notification) -> None:
        self.sent.append(notification)


@pytest.fixture(autouse=True)
def fresh_metrics():
    """Model call numbers are process wide, so each test starts from zero."""
    from app import metrics

    metrics.reset_for_tests()
    yield


@pytest.fixture()
def notifier():
    return RecordingNotifier()


@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture()
def starter():
    return RecordingStarter()


@pytest.fixture()
def client(fake_redis, starter, notifier):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
    app.dependency_overrides[get_workflow_starter] = lambda: starter
    app.dependency_overrides[get_notifier] = lambda: notifier
    # A fresh saver per test, so review threads from one test never leak into the next.
    saver = InMemorySaver()
    app.dependency_overrides[get_checkpointer] = lambda: saver
    yield TestClient(app)
    app.dependency_overrides.clear()
