import json
from types import SimpleNamespace

import httpx
import pytest

from app import alerts, metrics
from app.config import Settings
from app.llm import AnthropicProvider, BedrockProvider, OllamaProvider
from app.main import app
from app.routes import get_llm_provider


def sample(name, **labels):
    return metrics.REGISTRY.get_sample_value(name, labels) or 0.0


def ollama(reply_tokens=(11, 7), status=200):
    def handler(request):
        if status != 200:
            return httpx.Response(status)
        body = {"message": {"content": "{}"}, "prompt_eval_count": reply_tokens[0], "eval_count": reply_tokens[1]}
        return httpx.Response(200, json=body)

    return OllamaProvider("http://h", "m1", client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture()
def priced(monkeypatch):
    settings = Settings(llm_price_input_per_mtok=3.0, llm_price_output_per_mtok=15.0)
    monkeypatch.setattr("app.llm.get_settings", lambda: settings)
    return settings


# --- each provider reports tokens, time and cost --------------------------------


def test_ollama_call_is_recorded_with_tokens_cost_and_purpose(priced):
    ollama().draft_soap("raw words")

    (rec,) = metrics.calls_since(0)
    assert (rec.provider, rec.model, rec.purpose, rec.ok) == ("ollama", "m1", "draft", True)
    assert (rec.input_tokens, rec.output_tokens) == (11, 7)
    assert rec.cost_usd == pytest.approx((11 * 3.0 + 7 * 15.0) / 1_000_000)
    assert rec.seconds >= 0
    assert sample("noteloop_llm_calls_total", provider="ollama", model="m1", purpose="draft", outcome="ok") == 1
    assert sample("noteloop_llm_tokens_total", provider="ollama", model="m1", direction="input") == 11
    assert sample("noteloop_llm_call_seconds_count", provider="ollama", model="m1", purpose="draft") == 1


def test_anthropic_and_bedrock_usage_are_read():
    class Messages:
        def create(self, **kw):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="{}")],
                usage=SimpleNamespace(input_tokens=5, output_tokens=2),
            )

    a = AnthropicProvider("k", "claude-test")
    a._client = SimpleNamespace(messages=Messages())
    a.suggest_wording({"plan": "x"})

    class Converse:
        def converse(self, **kw):
            return {"output": {"message": {"content": [{"text": "{}"}]}}, "usage": {"inputTokens": 9, "outputTokens": 4}}

    BedrockProvider("b-model", "us-west-2", client=Converse()).propose_fixes("r", {"plan": "p"}, [])

    anthropic_rec, bedrock_rec = metrics.calls_since(0)
    assert (anthropic_rec.purpose, anthropic_rec.input_tokens, anthropic_rec.output_tokens) == ("suggest", 5, 2)
    assert (bedrock_rec.purpose, bedrock_rec.input_tokens, bedrock_rec.output_tokens) == ("fixes", 9, 4)


def test_each_reviewer_call_is_labeled_by_role():
    ollama().review_as("safety", "raw", {"plan": "p"})
    ollama().review_note("raw", {"plan": "p"})
    assert [r.purpose for r in metrics.calls_since(0)] == ["review_safety", "review"]


def test_missing_usage_counts_as_zero_tokens():
    a = AnthropicProvider("k", "m")
    a._client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")]))
    )
    a.draft_soap("raw")
    (rec,) = metrics.calls_since(0)
    assert rec.ok and rec.input_tokens == 0 and rec.cost_usd == 0


def test_a_failed_call_is_recorded_and_still_raises():
    with pytest.raises(httpx.HTTPStatusError):
        ollama(status=500).draft_soap("raw")
    (rec,) = metrics.calls_since(0)
    assert rec.ok is False
    assert sample("noteloop_llm_calls_total", provider="ollama", model="m1", purpose="draft", outcome="error") == 1
    assert metrics.state().consecutive_failures == 1


def test_summary_reports_percentiles_and_totals():
    recs = [metrics.CallRecord("p", "m", "draft", s, True, 10, 5, 0.01) for s in (1.0, 2.0, 3.0, 4.0, 10.0)]
    recs.append(metrics.CallRecord("p", "m", "draft", 0.5, False))
    summary = metrics.summarize_calls(recs)
    assert summary["calls"] == 6 and summary["errors"] == 1
    assert summary["input_tokens"] == 50 and summary["cost_usd"] == pytest.approx(0.05)
    assert summary["p95_seconds"] == 10.0 and summary["p50_seconds"] == 2.0
    assert metrics.summarize_calls([])["p95_seconds"] == 0.0


# --- alert rules ------------------------------------------------------------------


def rec(ok=True, seconds=1.0, tokens=0, cost=0.0, slow_after=0.0):
    metrics.record_call(
        metrics.CallRecord("ollama", "m1", "draft", seconds, ok, tokens, 0, cost), slow_after
    )


def titles(settings, now=0.0):
    return [n.title for n in alerts.due_alerts(settings, now)]


def test_budget_alert_fires_once_per_day():
    settings = Settings(llm_daily_cost_budget_usd=1.0)
    rec(cost=0.4)
    assert titles(settings) == []
    rec(cost=0.7)
    assert titles(settings) == ["Model spend passed the daily budget"]
    rec(cost=0.5)
    assert titles(settings) == []  # already reported today


def test_token_budget_works_for_free_local_models():
    settings = Settings(llm_daily_token_budget=1000)
    rec(tokens=1200)
    (note,) = alerts.due_alerts(settings)
    assert note.fields["Tokens today"] == "1,200" and note.fields["Token budget"] == "1,000"


def test_a_new_day_resets_the_budget(monkeypatch):
    settings = Settings(llm_daily_cost_budget_usd=1.0)
    monkeypatch.setattr("app.metrics._today", lambda: "2026-10-18")
    rec(cost=2.0)
    assert len(alerts.due_alerts(settings)) == 1
    monkeypatch.setattr("app.metrics._today", lambda: "2026-10-19")
    rec(cost=0.2)
    assert alerts.due_alerts(settings) == []  # a new day starts at zero
    rec(cost=2.0)
    assert len(alerts.due_alerts(settings)) == 1


def test_failure_alert_fires_once_per_streak_and_rearms_after_a_success():
    settings = Settings(llm_failure_alert_threshold=3)
    for _ in range(2):
        rec(ok=False)
    assert titles(settings) == []
    rec(ok=False)
    assert titles(settings) == ["Model calls are failing"]
    rec(ok=False)
    assert titles(settings) == []  # same streak
    rec(ok=True)
    for _ in range(3):
        rec(ok=False)
    assert titles(settings) == ["Model calls are failing"]  # a new streak


def test_slow_call_alert_respects_the_cooldown():
    settings = Settings(llm_slow_call_seconds=5.0, llm_alert_cooldown_seconds=900)
    rec(seconds=2.0, slow_after=5.0)
    assert titles(settings, now=1000.0) == []  # fast enough
    rec(seconds=8.0, slow_after=5.0)
    rec(seconds=12.0, slow_after=5.0)
    (note,) = alerts.due_alerts(settings, now=1000.0)
    assert note.fields["Slow calls"] == "2" and note.fields["Slowest"] == "12.0s"
    rec(seconds=9.0, slow_after=5.0)
    assert titles(settings, now=1500.0) == []  # inside the cooldown
    rec(seconds=9.0, slow_after=5.0)
    assert titles(settings, now=2000.0) == ["Slow model calls"]


def test_rules_that_are_off_stay_quiet():
    rec(ok=False, seconds=99.0, tokens=10**9, cost=100.0, slow_after=0.0)
    assert alerts.due_alerts(Settings(llm_failure_alert_threshold=0)) == []


def test_alert_messages_carry_no_note_text():
    rec(ok=False)
    rec(ok=False)
    rec(ok=False)
    (note,) = alerts.due_alerts(Settings(llm_failure_alert_threshold=3))
    assert set(note.fields) == {"Failed in a row", "Provider", "Model"}


# --- through the API --------------------------------------------------------------

RAW = "Client reports poor sleep and anxiety before work meetings this week."


def test_metrics_endpoint_has_model_numbers_and_no_note_text(client):
    app.dependency_overrides[get_llm_provider] = lambda: ollama(
        reply_tokens=(20, 30)
    )
    # The fake model replies "{}", which is not a SOAP note, so this draft is rejected twice.
    assert client.post("/sessions/1/draft", json={"raw_input": RAW}).status_code == 502
    text = client.get("/metrics").text
    assert 'noteloop_llm_calls_total{model="m1",outcome="ok",provider="ollama",purpose="draft"} 2.0' in text
    assert 'noteloop_llm_tokens_total{direction="input",model="m1",provider="ollama"} 40.0' in text
    assert 'noteloop_llm_replies_rejected_total{what="SOAP note"} 2.0' in text
    assert "noteloop_llm_call_seconds_bucket" in text
    assert "sleep" not in text and "anxiety" not in text


def test_failing_model_sends_one_slack_alert_even_though_the_request_errors(client, notifier, monkeypatch):
    monkeypatch.setattr("app.alert_middleware.get_settings", lambda: Settings(llm_failure_alert_threshold=2))
    app.dependency_overrides[get_llm_provider] = lambda: ollama(status=500)
    for session_id in (1, 2, 3):
        assert client.post(f"/sessions/{session_id}/draft", json={"raw_input": RAW}).status_code == 502
    alerts_sent = [n for n in notifier.sent if n.title == "Model calls are failing"]
    assert len(alerts_sent) == 1  # three failed drafts, one alert
    assert RAW not in json.dumps([vars(n) for n in notifier.sent])


def test_guardrail_findings_are_counted(client):
    class Inventing:
        def draft_soap(self, raw_input):
            return json.dumps(
                {"subjective": "S.", "objective": "O.", "assessment": "Meets criteria for PTSD.", "plan": "P."}
            )

    app.dependency_overrides[get_llm_provider] = lambda: Inventing()
    assert client.post("/sessions/1/draft", json={"raw_input": RAW}).status_code == 201
    assert sample("noteloop_guardrail_findings_total", kind="ungrounded") == 1


def test_a_broken_notifier_setting_does_not_break_requests(client, monkeypatch, caplog):
    from app.routes import get_notifier

    def bad_notifier():
        raise ValueError("SLACK_WEBHOOK_URL must start with https://")

    monkeypatch.setattr("app.alert_middleware.get_settings", lambda: Settings(llm_failure_alert_threshold=1))
    app.dependency_overrides[get_notifier] = bad_notifier
    app.dependency_overrides[get_llm_provider] = lambda: ollama(status=500)
    res = client.post("/sessions/1/draft", json={"raw_input": RAW})
    assert res.status_code == 502  # the user still gets the normal error
    assert "Could not send model alerts" in caplog.text
