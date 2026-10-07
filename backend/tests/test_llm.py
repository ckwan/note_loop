import sys
from types import SimpleNamespace

from app.config import Settings
from app.llm import SYSTEM_PROMPT, OllamaProvider, build_provider


def test_build_provider_uses_local_ollama(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, host):
            captured["host"] = host

        def chat(self, *, model, messages):
            captured["model"] = model
            captured["messages"] = messages
            return SimpleNamespace(
                message=SimpleNamespace(content='{"subjective":"Not documented."}')
            )

    monkeypatch.setitem(sys.modules, "ollama", SimpleNamespace(Client=FakeClient))

    settings = Settings(_env_file=None)
    provider = build_provider(settings)
    result = provider.draft_soap("Client felt anxious.")

    assert isinstance(provider, OllamaProvider)
    assert captured["host"] == "http://localhost:11434"
    assert captured["model"] == "llama3.2"
    assert captured["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "<session_notes>\nClient felt anxious.\n</session_notes>",
        },
    ]
    assert result == '{"subjective":"Not documented."}'
