"""Checks the alert rules after every response, including error responses.

A background task would not do: FastAPI drops it when an endpoint raises, which is the
case that matters most for a failing model. This runs after the response is sent, so the
user never waits for it.
"""

import logging

from fastapi.concurrency import run_in_threadpool

from . import alerts
from .config import get_settings
from .notify.notifier import send_safely

logger = logging.getLogger(__name__)


def _send_due_alerts(app) -> None:
    due = alerts.due_alerts(get_settings())
    if not due:
        return
    # The same notifier the routes use, so a test that replaces it sees these messages too.
    from .routes import get_notifier

    notifier = app.dependency_overrides.get(get_notifier, get_notifier)()
    for notification in due:
        send_safely(notifier, notification)


class LLMAlertMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        await self.app(scope, receive, send)
        if scope["type"] == "http" and scope["path"] not in ("/health", "/metrics"):
            try:
                await run_in_threadpool(_send_due_alerts, scope["app"])
            except Exception:
                # The response is already sent. A bad Slack setting must not become a crash.
                logger.warning("Could not send model alerts", exc_info=False)
