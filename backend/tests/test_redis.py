from redis import Redis

from app.config import get_settings
from app.redis_client import get_redis_client
from app.main import app
from app.routes import get_workflow_starter

RAW = "Client reports poor sleep this week and feeling anxious before work meetings."


def draft(client, session_id=1):
    return client.post(f"/sessions/{session_id}/draft", json={"raw_input": RAW})


def sign(client, key=None, session_id=1):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"/sessions/{session_id}/note/sign", headers=headers)


def test_draft_rate_limit_applies_per_therapist(client, monkeypatch):
    monkeypatch.setattr("app.ratelimit._now", lambda: 1_000_000.0)
    monkeypatch.setattr(get_settings(), "draft_rate_limit_per_minute", 2)

    assert draft(client, 1).status_code == 201
    assert draft(client, 2).status_code == 201
    res = draft(client, 3)
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) >= 1


def test_rate_limit_fails_open_when_redis_is_down(client):
    dead = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    app.dependency_overrides[get_redis_client] = lambda: dead
    assert draft(client).status_code == 201


def test_sign_with_same_key_returns_same_response(client, starter):
    draft(client)
    first = sign(client, "key-1")
    second = sign(client, "key-1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    actions = [e["action"] for e in client.get("/sessions/1/note/audit").json()]
    assert actions.count("signed") == 1
    assert len(starter.started) == 1


def test_sign_with_a_new_key_after_signing_is_rejected(client):
    draft(client)
    assert sign(client, "key-1").status_code == 200
    assert sign(client, "key-2").status_code == 409


def test_sign_without_a_key_still_blocks_double_sign(client):
    draft(client)
    assert sign(client).status_code == 200
    assert sign(client).status_code == 409


def test_failed_sign_releases_the_key(client):
    # No note exists yet, so signing fails. The same key must work once there is one.
    assert sign(client, "key-1").status_code == 404
    draft(client)
    assert sign(client, "key-1").status_code == 200


def test_signing_creates_a_pending_claim(client):
    assert client.get("/sessions/1/claim").status_code == 404
    draft(client)
    assert client.get("/sessions/1/claim").status_code == 404
    sign(client)
    claim = client.get("/sessions/1/claim").json()
    assert claim["status"] == "pending"
    assert claim["payer_reference"] is None


def test_workflow_start_failure_does_not_fail_the_sign(client):
    class BrokenStarter:
        async def start_note_signed(self, note_id):
            raise RuntimeError("Temporal is down")

    app.dependency_overrides[get_workflow_starter] = lambda: BrokenStarter()
    draft(client)
    assert sign(client).status_code == 200
    assert client.get("/sessions/1/claim").json()["status"] == "pending"
