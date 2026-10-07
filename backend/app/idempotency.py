"""Idempotency keys for the sign endpoint.

A client sends an Idempotency-Key header. The first request with a key does the
work and stores the response. A repeat with the same key gets the stored response
back instead of an error. The database still blocks a double sign, so if Redis is
down this module fails open and the database is the safety net.
"""

import logging

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from .errors import ConflictError
from .schemas import NoteOut
from .services.notes import note_to_out, sign_note

logger = logging.getLogger(__name__)

TTL_SECONDS = 300
IN_PROGRESS = "in_progress"


def _key(scope: str, key: str) -> str:
    return f"idem:{scope}:{key}"


def _begin(r: Redis, scope: str, key: str) -> str | None:
    """Return None if this caller now owns the key, else the stored value."""
    try:
        if r.set(_key(scope, key), IN_PROGRESS, nx=True, ex=TTL_SECONDS):
            return None
        return r.get(_key(scope, key))
    except RedisError:
        logger.warning("Redis unavailable, skipping idempotency check")
        return None


def _finish(r: Redis, scope: str, key: str, payload: str) -> None:
    try:
        r.set(_key(scope, key), payload, ex=TTL_SECONDS)
    except RedisError:
        logger.warning("Could not store idempotent response")


def _abandon(r: Redis, scope: str, key: str) -> None:
    try:
        r.delete(_key(scope, key))
    except RedisError:
        logger.warning("Could not release idempotency key")


def sign_once(
    db: Session, r: Redis, session_id: int, key: str | None
) -> tuple[NoteOut, bool]:
    """Sign a note. Returns the response and whether it was replayed from a prior call."""
    if not key:
        return note_to_out(sign_note(db, session_id)), False

    scope = f"sign:{session_id}"
    existing = _begin(r, scope, key)
    if existing is not None:
        if existing == IN_PROGRESS:
            raise ConflictError("A sign request with this key is already in progress")
        return NoteOut.model_validate_json(existing), True

    try:
        out = note_to_out(sign_note(db, session_id))
    except Exception:
        _abandon(r, scope, key)
        raise
    _finish(r, scope, key, out.model_dump_json())
    return out, False
