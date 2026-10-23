"""Cost, latency and error tracking for model calls.

Every call to a model is recorded here, whichever provider made it. The numbers feed
three things: a Prometheus style /metrics endpoint, the alert rules in alerts.py, and
the cost and latency table in the evals.

Only counts, token numbers, seconds and short labels are kept. Never note text.
"""

import math
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

REGISTRY = CollectorRegistry()

CALLS = Counter(
    "noteloop_llm_calls_total", "Model calls.", ["provider", "model", "purpose", "outcome"], registry=REGISTRY
)
TOKENS = Counter(
    "noteloop_llm_tokens_total", "Tokens the provider reported.", ["provider", "model", "direction"], registry=REGISTRY
)
COST = Counter(
    "noteloop_llm_cost_usd_total", "Estimated spend in US dollars.", ["provider", "model"], registry=REGISTRY
)
LATENCY = Histogram(
    "noteloop_llm_call_seconds",
    "Seconds per model call.",
    ["provider", "model", "purpose"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    registry=REGISTRY,
)
REJECTED = Counter(
    "noteloop_llm_replies_rejected_total", "Replies that failed validation.", ["what"], registry=REGISTRY
)
GUARDRAILS = Counter(
    "noteloop_guardrail_findings_total", "Findings raised by the rule based checks.", ["kind"], registry=REGISTRY
)
NOTE_EDIT_RATE = Histogram(
    "noteloop_note_edit_rate",
    "Share of the AI draft the therapist changed before signing. 0 is signed as written.",
    buckets=(0.0, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0),
    registry=REGISTRY,
)
SECTION_EDIT_RATE = Histogram(
    "noteloop_section_edit_rate",
    "Same measure for one SOAP section.",
    ["section"],
    buckets=(0.0, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0),
    registry=REGISTRY,
)


@dataclass
class CallRecord:
    provider: str
    model: str
    purpose: str  # draft, suggest, review, review_safety, fixes, ...
    seconds: float
    ok: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class _State:
    records: deque = field(default_factory=lambda: deque(maxlen=5000))
    total_calls: int = 0  # grows forever, so a reader can ask for "calls since N"
    day: str = ""
    day_tokens: int = 0
    day_cost: float = 0.0
    consecutive_failures: int = 0
    # What the alert rules have already reported, so one problem is one message.
    budget_alerted_day: str = ""
    failure_alerted: bool = False
    slow_alert_at: float = 0.0
    slow_calls: list[CallRecord] = field(default_factory=list)


_lock = threading.Lock()
_state = _State()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def cost_of(input_tokens: int, output_tokens: int, price_in: float, price_out: float) -> float:
    """Estimated dollars. Prices are per million tokens and come from settings."""
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def record_call(rec: CallRecord, slow_after: float = 0.0) -> None:
    outcome = "ok" if rec.ok else "error"
    CALLS.labels(rec.provider, rec.model, rec.purpose, outcome).inc()
    LATENCY.labels(rec.provider, rec.model, rec.purpose).observe(rec.seconds)
    if rec.input_tokens:
        TOKENS.labels(rec.provider, rec.model, "input").inc(rec.input_tokens)
    if rec.output_tokens:
        TOKENS.labels(rec.provider, rec.model, "output").inc(rec.output_tokens)
    if rec.cost_usd:
        COST.labels(rec.provider, rec.model).inc(rec.cost_usd)

    with _lock:
        today = _today()
        if _state.day != today:  # a new day starts a new budget
            _state.day, _state.day_tokens, _state.day_cost = today, 0, 0.0
        _state.records.append(rec)
        _state.total_calls += 1
        _state.day_tokens += rec.input_tokens + rec.output_tokens
        _state.day_cost += rec.cost_usd
        if rec.ok:
            _state.consecutive_failures = 0
            _state.failure_alerted = False
        else:
            _state.consecutive_failures += 1
        if slow_after and rec.seconds > slow_after:
            _state.slow_calls.append(rec)


def record_rejection(what: str) -> None:
    REJECTED.labels(what).inc()


def record_guardrail(kind: str) -> None:
    GUARDRAILS.labels(kind).inc()


def record_edit_rate(overall: float, per_section: dict[str, float]) -> None:
    NOTE_EDIT_RATE.observe(overall)
    for section, rate in per_section.items():
        SECTION_EDIT_RATE.labels(section).observe(rate)


def total_calls() -> int:
    with _lock:
        return _state.total_calls


def calls_since(start: int) -> list[CallRecord]:
    """Calls recorded after the first `start` calls. The evals use this to price a run."""
    with _lock:
        newest = _state.total_calls - start
        return list(_state.records)[-newest:] if newest > 0 else []


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def summarize_calls(records: list[CallRecord]) -> dict:
    seconds = [r.seconds for r in records]
    return {
        "calls": len(records),
        "errors": sum(1 for r in records if not r.ok),
        "input_tokens": sum(r.input_tokens for r in records),
        "output_tokens": sum(r.output_tokens for r in records),
        "cost_usd": sum(r.cost_usd for r in records),
        "p50_seconds": percentile(seconds, 0.50),
        "p95_seconds": percentile(seconds, 0.95),
    }


def render_prometheus() -> bytes:
    return generate_latest(REGISTRY)


def state() -> _State:
    """The live state. Alert rules read it under the lock through alerts.py."""
    return _state


def lock() -> threading.Lock:
    return _lock


def reset_for_tests() -> None:
    global _state
    with _lock:
        _state = _State()
    for metric in (CALLS, TOKENS, COST, LATENCY, REJECTED, GUARDRAILS, SECTION_EDIT_RATE):
        metric.clear()
