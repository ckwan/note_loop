import json

import httpx
import pytest
from temporalio.converter import DataConverter

from app.config import Settings
from app.db import SessionLocal
from app.models import Patient
from app.notify import messages
from app.notify.notifier import (
    NoopNotifier,
    NotifyError,
    SlackNotifier,
    build_notifier,
    send_safely,
    to_slack_payload,
)
from app.notify.types import Notification

WEBHOOK = "https://hooks.slack.com/services/T000/B000/SECRETSECRET"


def slack_with(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SlackNotifier(WEBHOOK, client=client)


def test_posts_a_message_with_blocks_and_a_plain_fallback():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text="ok")

    slack_with(handler).send(messages.batch_finished(10, 8, 2))

    assert seen["url"] == WEBHOOK
    assert seen["body"]["text"] == "Resubmit batch finished"
    texts = json.dumps(seen["body"]["blocks"])
    assert "Failed again" in texts and "warning" in texts


def test_link_becomes_a_context_block():
    payload = to_slack_payload(
        Notification(title="t", link="http://localhost:8233/x", link_text="Open in Temporal")
    )
    assert payload["blocks"][-1]["type"] == "context"
    assert "<http://localhost:8233/x|Open in Temporal>" in json.dumps(payload)


def test_markup_characters_are_escaped():
    payload = to_slack_payload(Notification(title="a <b> & c", fields={"k": "<@here>"}))
    text = json.dumps(payload)
    assert "&lt;b&gt;" in text and "&amp;" in text
    assert "<@here>" not in text  # would have pinged the channel


def test_slack_rejection_raises_without_leaking_the_url():
    notifier = slack_with(lambda request: httpx.Response(404, text="no_service"))
    with pytest.raises(NotifyError) as err:
        notifier.send(Notification(title="t"))
    assert "404" in str(err.value)
    assert "SECRETSECRET" not in str(err.value)
    assert "hooks.slack.com" not in str(err.value)


def test_network_failure_raises_without_leaking_the_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(NotifyError) as err:
        slack_with(handler).send(Notification(title="t"))
    assert "ConnectError" in str(err.value)
    assert "SECRETSECRET" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_webhook_must_be_https():
    with pytest.raises(ValueError, match="https"):
        SlackNotifier("http://hooks.slack.com/services/x")


def test_no_webhook_means_no_op():
    assert isinstance(build_notifier(Settings(slack_webhook_url=None)), NoopNotifier)
    assert isinstance(build_notifier(Settings(slack_webhook_url=WEBHOOK)), SlackNotifier)


def test_send_safely_swallows_failures():
    notifier = slack_with(lambda request: httpx.Response(500))
    send_safely(notifier, Notification(title="t"))  # must not raise


def test_a_notification_survives_the_temporal_data_converter():
    converter = DataConverter.default.payload_converter
    original = messages.review_needs_attention(7, 2, [("plan", "not_documented")])
    payloads = converter.to_payloads([original])
    (restored,) = converter.from_payloads(payloads, [Notification])
    assert restored == original


def test_messages_read_correctly():
    assert messages.batch_finished(0, 0, 0).fields == {"Processed": "0"}
    assert messages.batch_finished(5, 5, 0).level == "success"
    assert messages.batch_finished(5, 3, 2).level == "warning"
    assert messages.claim_failed(12).level == "error"
    assert messages.daily_summary({"failed": 0}).level == "success"
    assert messages.daily_summary({"failed": 3}).level == "warning"


def test_no_message_can_carry_a_patient_name_or_note_text(client):
    with SessionLocal() as db:
        names = [p.display_name for p in db.query(Patient)]
    assert names

    built = [
        messages.claim_failed(1),
        messages.batch_started(25),
        messages.batch_finished(3, 2, 1),
        messages.review_needs_attention(1, 2, [("assessment", "ungrounded")]),
        messages.daily_summary({"pending": 1, "created": 2, "submitted": 3, "failed": 4}),
    ]
    blob = json.dumps([to_slack_payload(n) for n in built]).lower()
    for name in names:
        assert name.lower() not in blob
