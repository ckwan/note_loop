import json
import os

import httpx
import pytest

from app.config import Settings
from app.llm import AnthropicProvider, OllamaProvider, StubProvider, build_provider
from app.tracing import configure_tracing


def make_ollama(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaProvider("http://host:11434/", "llama3.1:8b", client=client)


def test_ollama_sends_expected_request_and_returns_content():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}})

    out = make_ollama(handler).draft_soap("client calm")
    body = seen["body"]
    assert seen["url"] == "http://host:11434/api/chat"
    assert body["model"] == "llama3.1:8b"
    assert body["stream"] is False and body["format"] == "json"
    assert body["options"] == {"temperature": 0, "num_predict": 1200}
    assert body["messages"][0]["role"] == "system"
    assert "client calm" in body["messages"][1]["content"]
    assert out == '{"ok": true}'


def test_ollama_http_error_raises():
    provider = make_ollama(lambda r: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        provider.draft_soap("x")


def test_build_provider_picks_each_and_rejects_typos():
    assert isinstance(build_provider(Settings(llm_provider="stub")), StubProvider)
    assert isinstance(build_provider(Settings(llm_provider="ollama")), OllamaProvider)
    assert isinstance(
        build_provider(Settings(llm_provider="anthropic", anthropic_api_key="k")),
        AnthropicProvider,
    )
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        build_provider(Settings(llm_provider="ollamma"))


def test_tracing_off_without_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert configure_tracing(Settings(langsmith_tracing=True, langsmith_api_key=None)) is False
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_tracing_hides_inputs_by_default(monkeypatch):
    for k in ("LANGSMITH_TRACING", "LANGSMITH_HIDE_INPUTS", "LANGSMITH_HIDE_OUTPUTS"):
        monkeypatch.delenv(k, raising=False)
    on = configure_tracing(Settings(langsmith_tracing=True, langsmith_api_key="k"))
    assert on is True
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
    assert os.environ["LANGSMITH_HIDE_OUTPUTS"] == "true"
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
