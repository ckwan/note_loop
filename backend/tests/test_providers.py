import json
from types import SimpleNamespace

import pytest

from app.llm import (
    FIX_PROMPT,
    ROLE_PROMPTS,
    REVIEW_PROMPT,
    SUGGEST_PROMPT,
    SYSTEM_PROMPT,
    AnthropicProvider,
    BedrockProvider,
    build_provider,
)
from app.config import Settings
from app.errors import LLMOutputError
from app.services.notes import _generate

NOTE = {"subjective": "S text", "objective": "O text", "assessment": "A text", "plan": "P text"}
FINDINGS = [{"section": "plan", "kind": "not_documented", "message": "Empty."}]

# Every call a provider can make: how to invoke it, which prompt it must use, the
# exact user message it must send, and its token limit. This pins the prompts, so
# a refactor cannot change what the model is asked.
CALLS = {
    "draft": (
        lambda p: p.draft_soap("raw words"),
        SYSTEM_PROMPT,
        "<session_notes>\nraw words\n</session_notes>",
        1200,
    ),
    "suggest": (
        lambda p: p.suggest_wording({"plan": "weekly"}),
        SUGGEST_PROMPT,
        '<note_sections>\n{"plan": "weekly"}\n</note_sections>',
        2000,
    ),
    "review": (
        lambda p: p.review_note("raw words", NOTE),
        REVIEW_PROMPT,
        f"<session_notes>\nraw words\n</session_notes>\n<note>\n{json.dumps(NOTE)}\n</note>",
        1500,
    ),
    **{
        f"review_{role}": (
            lambda p, role=role: p.review_as(role, "raw words", NOTE),
            ROLE_PROMPTS[role],
            f"<session_notes>\nraw words\n</session_notes>\n<note>\n{json.dumps(NOTE)}\n</note>",
            1500,
        )
        for role in ROLE_PROMPTS
    },
    "fixes": (
        lambda p: p.propose_fixes("raw words", NOTE, FINDINGS),
        FIX_PROMPT,
        (
            f"<session_notes>\nraw words\n</session_notes>\n<note>\n{json.dumps(NOTE)}\n</note>\n"
            f"<findings>\n{json.dumps(FINDINGS)}\n</findings>"
        ),
        2000,
    ),
}


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text='{"a": '),
                SimpleNamespace(type="tool_use", text="ignored"),
                SimpleNamespace(type="text", text="1}"),
            ]
        )


@pytest.fixture()
def anthropic_provider():
    provider = AnthropicProvider("fake-key", "claude-test")
    provider._client = SimpleNamespace(messages=FakeMessages())
    return provider


@pytest.mark.parametrize("name", CALLS)
def test_anthropic_sends_the_expected_prompt(anthropic_provider, name):
    call, system, user, max_tokens = CALLS[name]

    result = call(anthropic_provider)

    (sent,) = anthropic_provider._client.messages.calls
    assert sent["model"] == "claude-test"
    assert sent["system"] == system
    assert sent["max_tokens"] == max_tokens
    assert sent["messages"] == [{"role": "user", "content": user}]
    assert result == '{"a": 1}'  # text blocks joined, other block types skipped


class FakeBedrock:
    """Stands in for boto3's bedrock-runtime client."""

    def __init__(self, error: Exception | None = None):
        self.calls = []
        self.error = error

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": '{"a": '}, {"toolUse": {"name": "ignored"}}, {"text": "1}"}],
                }
            }
        }


@pytest.fixture()
def bedrock_provider():
    return BedrockProvider("us.test-model-v1:0", "us-west-2", client=FakeBedrock())


@pytest.mark.parametrize("name", CALLS)
def test_bedrock_sends_the_same_prompts_through_converse(bedrock_provider, name):
    call, system, user, max_tokens = CALLS[name]

    result = call(bedrock_provider)

    (sent,) = bedrock_provider._client.calls
    assert sent["modelId"] == "us.test-model-v1:0"
    assert sent["system"] == [{"text": system}]
    assert sent["messages"] == [{"role": "user", "content": [{"text": user}]}]
    assert sent["inferenceConfig"] == {"maxTokens": max_tokens, "temperature": 0}
    assert result == '{"a": 1}'  # text blocks joined, other block types skipped


def test_bedrock_needs_a_model_id():
    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        BedrockProvider("", "us-west-2", client=FakeBedrock())


def test_a_bedrock_failure_becomes_an_llm_error_without_leaking_details():
    provider = BedrockProvider("m", "us-west-2", client=FakeBedrock(error=RuntimeError("AccessDenied: arn:aws:iam::123:role/x")))
    with pytest.raises(LLMOutputError) as caught:
        _generate(lambda: provider.draft_soap("raw"), lambda t: t, "SOAP note")
    assert "arn:aws" not in str(caught.value)  # the API returns a plain 502 message


def test_build_provider_makes_a_bedrock_provider_without_calling_aws():
    provider = build_provider(Settings(llm_provider="bedrock", bedrock_model_id="m", bedrock_region="us-west-2"))
    assert isinstance(provider, BedrockProvider)
    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        build_provider(Settings(llm_provider="bedrock"))
