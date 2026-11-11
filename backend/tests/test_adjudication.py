"""Adjudication mode, against a mocked Claims API. No network."""

import dataclasses
import json

import httpx
import pytest
from pydantic import ValidationError
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import AuditEvent, Claim, Note
from app.payer.claims_api import ClaimsApiClient
from app.workflows.activities import check_claim_decision, prepare_claim, submit_claim
from app.workflows.workflows import NoteSignedWorkflow
from tests.test_activities import RAW, audit_actions, claim_for, signed_note_id
from tests.test_workflow import _Recorder, run_workflow, titles, use_notifier

ORG = "org-acme"


class FakeClaimsApi:
    """Behaves like the contract: POST dedupes on external_ref, GET returns a status."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.claims: dict[str, dict] = {}
        self.post_errors: list[int | Exception] = []  # consumed one per POST
        self.statuses: list[str] = ["approved"]  # consumed one per GET, last repeats

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            if self.post_errors:
                err = self.post_errors.pop(0)
                if isinstance(err, Exception):
                    raise err
                return httpx.Response(err, json={"detail": "x"})
            if request.headers.get("X-Org-Id") != body["org_id"]:
                return httpx.Response(403, json={"detail": "org mismatch"})
            if not body["external_ref"].strip():
                return httpx.Response(422, json={"detail": "bad ref"})
            ref = body["external_ref"]
            if ref in self.claims:
                return httpx.Response(200, json=self.claims[ref])
            claim = {"id": f"c-{len(self.claims) + 1}", "status": "open",
                     "external_ref": ref, "updated_at": "2026-01-01T00:00:00Z"}
            self.claims[ref] = claim
            return httpx.Response(201, json=claim)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return httpx.Response(200, json={"id": request.url.path.split("/")[-1],
                                         "status": status, "external_ref": "x",
                                         "updated_at": "2026-01-01T00:00:00Z"})

    def posts(self):
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture()
def api(client, monkeypatch):
    fake = FakeClaimsApi()
    settings = get_settings()
    monkeypatch.setattr(settings, "payer_mode", "adjudication")
    monkeypatch.setattr(settings, "claims_api_url", "http://claims.test")
    monkeypatch.setattr(settings, "claims_org_id", ORG)
    monkeypatch.setattr(settings, "claims_poll_interval_seconds", 1.0)
    monkeypatch.setattr(settings, "claims_poll_max_wait_seconds", 5.0)
    monkeypatch.setattr(
        "app.workflows.activities.build_claims_client",
        lambda s: ClaimsApiClient(s, transport=httpx.MockTransport(fake.handler)),
    )
    return fake


def new_claim(client) -> tuple[int, int]:
    note_id = signed_note_id(client)
    return note_id, ActivityEnvironment().run(prepare_claim, note_id)


# --- config -------------------------------------------------------------------

def test_unknown_payer_mode_fails_at_startup():
    with pytest.raises(ValidationError):
        Settings(payer_mode="bogus")


@pytest.mark.parametrize("url,org", [(None, "o"), ("http://x", None), ("http://x", "  ")])
def test_adjudication_needs_url_and_org(url, org):
    with pytest.raises(ValidationError):
        Settings(payer_mode="adjudication", claims_api_url=url, claims_org_id=org)


def test_simulated_is_the_default():
    assert Settings().payer_mode == "simulated"


# --- submit -------------------------------------------------------------------

def test_submit_posts_the_contract_and_stores_the_remote_id(client, api):
    note_id, claim_id = new_claim(client)

    ref = ActivityEnvironment().run(submit_claim, claim_id)

    (post,) = api.posts()
    assert str(post.url) == "http://claims.test/claims"
    assert post.headers["X-Org-Id"] == ORG
    body = json.loads(post.content)
    assert set(body) == {"org_id", "external_ref", "claimant_name", "amount", "history_notes"}
    assert body["org_id"] == ORG and body["external_ref"] == str(note_id)
    claim = claim_for(note_id)
    assert claim.status == "submitted" and claim.remote_claim_id == "c-1" == ref


def test_a_repeated_submit_does_not_create_a_duplicate(client, api):
    note_id, claim_id = new_claim(client)
    env = ActivityEnvironment()
    env.run(submit_claim, claim_id)
    # Simulate a retry after the database write was lost: the API says 200.
    with SessionLocal() as db:
        c = db.get(Claim, claim_id)
        c.status, c.remote_claim_id = "created", None
        db.commit()

    env.run(submit_claim, claim_id)

    assert len(api.claims) == 1
    assert claim_for(note_id).remote_claim_id == "c-1"


def test_5xx_and_timeouts_are_retryable_then_succeed(client, api):
    _, claim_id = new_claim(client)
    api.post_errors = [503, httpx.ReadTimeout("slow")]
    env = ActivityEnvironment()
    for _ in range(2):
        with pytest.raises(ApplicationError) as exc:
            env.run(submit_claim, claim_id)
        assert exc.value.non_retryable is False
    assert env.run(submit_claim, claim_id) == "c-1"


@pytest.mark.parametrize("code", [400, 404, 422])
def test_other_4xx_is_not_retryable(client, api, code):
    _, claim_id = new_claim(client)
    api.post_errors = [code]
    with pytest.raises(ApplicationError) as exc:
        ActivityEnvironment().run(submit_claim, claim_id)
    assert exc.value.non_retryable is True


def test_an_org_mismatch_is_a_non_retryable_403(client, api, monkeypatch):
    _, claim_id = new_claim(client)
    # Header and body disagree, which the API rejects.
    real = api.handler
    def mismatched(request):
        request.headers["X-Org-Id"] = "other-org"
        return real(request)
    monkeypatch.setattr(
        "app.workflows.activities.build_claims_client",
        lambda s: ClaimsApiClient(s, transport=httpx.MockTransport(mismatched)),
    )
    with pytest.raises(ApplicationError) as exc:
        ActivityEnvironment().run(submit_claim, claim_id)
    assert exc.value.non_retryable is True and "403" in str(exc.value)


def test_same_org_value_goes_in_header_and_body(client, api):
    _, claim_id = new_claim(client)
    ActivityEnvironment().run(submit_claim, claim_id)
    (post,) = api.posts()
    assert post.headers["X-Org-Id"] == json.loads(post.content)["org_id"]


# --- privacy ------------------------------------------------------------------

def test_the_outgoing_request_has_no_note_text_session_notes_or_patient_name(client, api):
    secret_note = "ZEBRA-SECRET-SOAP-TEXT"
    secret_raw = "QUOKKA-SESSION-NOTES about panic attacks"
    client.post("/sessions/1/draft", json={"raw_input": secret_raw})
    client.post("/sessions/1/note/sign")
    with SessionLocal() as db:
        note = db.query(Note).one()
        note.final_note = {"subjective": secret_note, "objective": secret_note,
                           "assessment": secret_note, "plan": secret_note}
        patient_name = note.session.patient.display_name
        note_id = note.id
        db.commit()
    claim_id = ActivityEnvironment().run(prepare_claim, note_id)

    ActivityEnvironment().run(submit_claim, claim_id)
    ActivityEnvironment().run(check_claim_decision, claim_id)

    assert api.requests
    for req in api.requests:
        wire = (req.url.path + json.dumps({k: v for k, v in req.headers.items() if k != "host"}) + req.content.decode()).lower()
        for secret in (secret_note, secret_raw, "panic attacks", patient_name,
                       *patient_name.split()):
            assert secret.lower() not in wire
    body = json.loads(api.posts()[0].content)
    assert body["history_notes"] == "Outpatient session claim"
    assert body["claimant_name"].startswith("pt_")


# --- decision tracking ----------------------------------------------------------

def test_check_decision_records_each_status_change_once(client, api):
    note_id, claim_id = new_claim(client)
    ActivityEnvironment().run(submit_claim, claim_id)
    api.statuses = ["under_review", "under_review", "approved"]
    env = ActivityEnvironment()

    states = [env.run(check_claim_decision, claim_id).state for _ in range(3)]
    env.run(check_claim_decision, claim_id)  # repeating a final answer changes nothing

    assert states == ["pending", "pending", "approved"]
    assert claim_for(note_id).status == "approved"
    actions = audit_actions(note_id)
    assert actions.count("claim_remote_status") == 2  # open -> under_review -> approved (open is recorded at submit)
    assert actions.count("claim_approved") == 1


def test_workflow_submit_then_approved(client, api, monkeypatch):
    rec = _Recorder()
    use_notifier(monkeypatch, rec)
    api.statuses = ["under_review", "approved"]
    note_id = signed_note_id(client)

    run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")

    assert claim_for(note_id).status == "approved"
    assert rec.sent == []


def test_workflow_denied_sends_slack_without_phi(client, api, monkeypatch):
    rec = _Recorder()
    use_notifier(monkeypatch, rec)
    api.statuses = ["denied"]
    note_id = signed_note_id(client)

    run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")

    assert claim_for(note_id).status == "denied"
    assert titles(rec) == ["Claim denied"]
    assert set(rec.sent[0].fields) == {"Note", "Claim", "Status"}


def test_workflow_timeout_moves_to_manual_follow_up(client, api, monkeypatch):
    rec = _Recorder()
    use_notifier(monkeypatch, rec)
    api.statuses = ["under_review"]  # never final
    note_id = signed_note_id(client)

    run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")  # does not raise

    assert claim_for(note_id).status == "manual_follow_up"
    assert "claim_follow_up_needed" in audit_actions(note_id)
    assert titles(rec) == ["Claim needs manual follow up"]


def test_workflow_retries_a_5xx_on_submit(client, api):
    api.post_errors = [500, 502]
    note_id = signed_note_id(client)
    run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")
    assert claim_for(note_id).status == "approved"
    assert len(api.claims) == 1


def test_workflow_marks_failed_on_a_non_retryable_4xx(client, api):
    api.post_errors = [422]
    note_id = signed_note_id(client)
    with pytest.raises(WorkflowFailureError):
        run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")
    assert claim_for(note_id).status == "failed"


def test_simulated_mode_does_not_touch_the_claims_api(client, api, monkeypatch):
    monkeypatch.setattr(get_settings(), "payer_mode", "simulated")
    note_id = signed_note_id(client)
    run_workflow(NoteSignedWorkflow.run, note_id, f"test-{note_id}")
    assert claim_for(note_id).status == "submitted"
    assert api.requests == []
