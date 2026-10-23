"""Rules that turn model call numbers into Slack messages.

Each rule fires once per problem, not once per call: a budget alert once a day, a failure
alert once per streak, and slow call alerts at most once per cooldown. Messages carry
counts, seconds and model names only.
"""

import time

from . import metrics
from .config import Settings
from .notify import messages
from .notify.types import Notification


def due_alerts(settings: Settings, now: float | None = None) -> list[Notification]:
    now = time.monotonic() if now is None else now
    due: list[Notification] = []
    with metrics.lock():
        st = metrics.state()

        over_cost = settings.llm_daily_cost_budget_usd and st.day_cost >= settings.llm_daily_cost_budget_usd
        over_tokens = settings.llm_daily_token_budget and st.day_tokens >= settings.llm_daily_token_budget
        if st.day and (over_cost or over_tokens) and st.budget_alerted_day != st.day:
            st.budget_alerted_day = st.day
            due.append(
                messages.llm_budget_exceeded(
                    st.day_tokens, st.day_cost, settings.llm_daily_cost_budget_usd, settings.llm_daily_token_budget
                )
            )

        threshold = settings.llm_failure_alert_threshold
        if threshold and st.consecutive_failures >= threshold and not st.failure_alerted:
            st.failure_alerted = True
            last = st.records[-1] if st.records else None
            due.append(
                messages.llm_failures(
                    st.consecutive_failures, last.provider if last else "unknown", last.model if last else "unknown"
                )
            )

        if st.slow_calls:
            if now - st.slow_alert_at >= settings.llm_alert_cooldown_seconds:
                slowest = max(st.slow_calls, key=lambda r: r.seconds)
                due.append(
                    messages.llm_slow(
                        len(st.slow_calls), slowest.seconds, settings.llm_slow_call_seconds, slowest.purpose, slowest.model
                    )
                )
                st.slow_alert_at = now
            # Slow calls inside the cooldown are not reported one by one. The latency
            # histogram in /metrics still has them.
            st.slow_calls = []
    return due
