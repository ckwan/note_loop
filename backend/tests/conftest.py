import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["LLM_PROVIDER"] = "stub"
os.environ["TEMPORAL_ENABLED"] = "false"

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.redis_client import get_redis_client
from app.routes import get_workflow_starter
from app.seed import seed


class RecordingStarter:
    """Stands in for Temporal. Records which notes would have started a workflow."""

    def __init__(self) -> None:
        self.started: list[int] = []

    async def start_note_signed(self, note_id: int) -> None:
        self.started.append(note_id)


@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture()
def starter():
    return RecordingStarter()


@pytest.fixture()
def client(fake_redis, starter):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    app.dependency_overrides.clear()
    app.dependency_overrides[get_redis_client] = lambda: fake_redis
    app.dependency_overrides[get_workflow_starter] = lambda: starter
    yield TestClient(app)
    app.dependency_overrides.clear()
