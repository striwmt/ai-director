"""TransformersDirectorProvider: in-process director LLM.

The model itself is never loaded here — these tests cover the provider
contract (factory wiring, structured-output repair loop, thinking-block
stripping) with the generation step stubbed out.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from aidirector.ai.providers.director import (
    TransformersDirectorProvider,
    _strip_thinking,
)
from aidirector.ai.runtime import _make_director_provider
from aidirector.ai.schemas import Message
from aidirector.config import ModelEndpointConfig
from aidirector.errors import StructuredOutputError


class _Answer(BaseModel):
    title: str
    count: int


def make_provider() -> TransformersDirectorProvider:
    cfg = ModelEndpointConfig(
        provider="transformers", model="Qwen/Qwen3-8B",
        extra={"quantization": "4bit"},
    )
    return TransformersDirectorProvider(cfg)


def test_factory_and_label():
    cfg = ModelEndpointConfig(provider="transformers", model="Qwen/Qwen3-8B")
    provider = _make_director_provider(cfg)
    assert isinstance(provider, TransformersDirectorProvider)
    # Constructing must stay cheap: no torch import, no weights.
    assert provider.name == "transformers:Qwen/Qwen3-8B"
    assert getattr(provider, "gpu_resident", True), "evicted by phase execution"


def test_strip_thinking():
    assert _strip_thinking("<think>hmm</think>\n{\"a\": 1}") == "\n{\"a\": 1}"
    assert _strip_thinking("{\"a\": 1}") == "{\"a\": 1}"


def _stub_generation(provider, outputs: list[str]) -> list[list[dict]]:
    """Replace the model call with canned outputs; records each chat."""
    calls: list[list[dict]] = []

    def fake_generate(chat: list[dict], thinking: bool) -> str:
        calls.append([dict(m) for m in chat])
        return outputs[len(calls) - 1]

    provider._model = object()  # skip load()
    provider._generate_sync = fake_generate
    return calls


async def test_generate_structured_valid_first_try():
    provider = make_provider()
    calls = _stub_generation(
        provider, [json.dumps({"title": "quiet trip", "count": 3})]
    )
    answer = await provider.generate_structured(
        [Message(role="user", content="plan")], _Answer
    )
    assert answer == _Answer(title="quiet trip", count=3)
    # The schema instruction leads the conversation (strict chat templates
    # reject system messages anywhere but the start).
    assert calls[0][0]["role"] == "system"
    assert "JSON schema" in calls[0][0]["content"]
    assert calls[0][-1]["role"] == "user"


async def test_generate_structured_repairs_invalid_output():
    provider = make_provider()
    calls = _stub_generation(
        provider,
        [
            "not json at all",
            json.dumps({"title": "fixed", "count": 1}),
        ],
    )
    answer = await provider.generate_structured(
        [Message(role="user", content="plan")], _Answer
    )
    assert answer.title == "fixed"
    assert len(calls) == 2
    # The failed attempt and the validation error are fed back.
    assert calls[1][-2]["role"] == "assistant"
    assert calls[1][-1]["role"] == "user"
    assert "failed validation" in calls[1][-1]["content"]


async def test_generate_structured_gives_up_after_max_attempts():
    provider = make_provider()
    calls = _stub_generation(provider, ["nope", "nope", "nope"])
    with pytest.raises(StructuredOutputError, match="_Answer"):
        await provider.generate_structured(
            [Message(role="user", content="plan")], _Answer
        )
    assert len(calls) == 3
