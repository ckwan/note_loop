import logging
from typing import Protocol

import httpx

from app.config import Settings
from app.notify.types import Notification

logger = logging.getLogger(__name__)

_EMOJI = {
    "info": ":information_source:",
    "success": ":white_check_mark:",
    "warning": ":warning:",
    "error": ":rotating_light:",
}


class NotifyError(Exception):
    """A notification could not be sent. The message never contains the webhook URL."""


class Notifier(Protocol):
    def send(self, notification: Notification) -> None: ...


class NoopNotifier:
    """Used when no webhook is configured, and in tests."""

    def send(self, notification: Notification) -> None:
        return None


def _escape(text: str) -> str:
    # Slack treats these three characters as markup.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_slack_payload(n: Notification) -> dict:
    heading = f"{_EMOJI[n.level]} *{_escape(n.title)}*"
    if n.body:
        heading += f"\n{_escape(n.body)}"
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": heading}}]
    if n.fields:
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*{_escape(k)}*\n{_escape(v)}"}
                    for k, v in list(n.fields.items())[:10]  # Slack allows 10 fields
                ],
            }
        )
    if n.link:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"<{n.link}|{_escape(n.link_text)}>"}
                ],
            }
        )
    return {"text": n.title, "blocks": blocks}  # text is the plain fallback


class SlackNotifier:
    """Posts to a Slack incoming webhook."""

    def __init__(self, webhook_url: str, client: httpx.Client | None = None) -> None:
        if not webhook_url.startswith("https://"):
            raise ValueError("SLACK_WEBHOOK_URL must start with https://")
        self._url = webhook_url
        self._client = client or httpx.Client(timeout=5.0)

    def send(self, notification: Notification) -> None:
        # httpx puts the full URL in its error text, and the URL is a secret.
        # Raise our own errors so it never reaches a log or a Temporal history.
        try:
            response = self._client.post(self._url, json=to_slack_payload(notification))
        except httpx.HTTPError as exc:
            raise NotifyError(f"Could not reach Slack ({type(exc).__name__})") from None
        if response.status_code != 200:
            raise NotifyError(f"Slack rejected the message (HTTP {response.status_code})")


def build_notifier(settings: Settings) -> Notifier:
    if settings.slack_webhook_url:
        return SlackNotifier(settings.slack_webhook_url)
    return NoopNotifier()


def send_safely(notifier: Notifier, notification: Notification) -> None:
    """Send, and never raise. Used where a failed notification must not matter."""
    try:
        notifier.send(notification)
    except Exception:
        logger.warning("Slack notification failed: %s", notification.title, exc_info=False)
