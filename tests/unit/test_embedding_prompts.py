"""Prompt-aware embedding: asymmetric models get query/document prompts."""

from __future__ import annotations

import asyncio

from aidirector.ai.providers.embedding import SentenceTransformersEmbeddingProvider
from aidirector.config import ModelEndpointConfig


class _FakeST:
    """Stands in for a SentenceTransformer with retrieval prompts."""

    def __init__(self, prompts):
        self.prompts = prompts
        self.calls = []

    def encode(self, inputs, **kwargs):
        self.calls.append(kwargs)
        return [[0.1, 0.2] for _ in inputs]


def test_prompt_passed_only_when_model_defines_it():
    provider = SentenceTransformersEmbeddingProvider(
        ModelEndpointConfig(provider="sentence-transformers", model="m")
    )
    asym = _FakeST({"query": "query: ", "document": "passage: "})
    provider._model = asym
    asyncio.run(provider.embed_text(["x"], prompt_name="query"))
    assert asym.calls[-1].get("prompt_name") == "query"
    asyncio.run(provider.embed_text(["x"], prompt_name="document"))
    assert asym.calls[-1].get("prompt_name") == "document"

    sym = _FakeST({})  # symmetric model: no prompts defined
    provider._model = sym
    asyncio.run(provider.embed_text(["x"], prompt_name="query"))
    assert "prompt_name" not in sym.calls[-1]
    asyncio.run(provider.embed_text(["x"]))
    assert "prompt_name" not in sym.calls[-1]
