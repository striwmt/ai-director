"""Shared helpers for OpenAI-compatible HTTP providers.

vLLM / llama.cpp server / SGLang all speak this dialect (AGENT.md §26).
Business logic never touches these HTTP details.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

from ...config import ModelEndpointConfig
from ...errors import ProviderError


def make_client(cfg: ModelEndpointConfig, timeout: float = 300.0) -> httpx.AsyncClient:
    if not cfg.base_url:
        raise ProviderError(f"provider '{cfg.provider}' requires base_url")
    headers = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    return httpx.AsyncClient(base_url=cfg.base_url, headers=headers, timeout=timeout)


async def chat_completion(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict[str, Any]],
    *,
    response_format: dict[str, Any] | None = None,
    temperature: float = 0.4,
    max_tokens: int | None = None,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    try:
        response = await client.post("/chat/completions", json=payload)
    except httpx.ConnectError as exc:
        raise ProviderError(
            f"cannot reach the LLM server at {client.base_url} ({exc}). "
            "Is it running? For the director, set provider: llama-server in "
            "config/models.yaml to have AI Director start it automatically "
            "(requires llama.cpp installed)."
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"chat completion request failed: {exc}") from exc
    if response.status_code >= 400:
        raise ProviderError(
            f"chat completion HTTP {response.status_code}: {response.text[:500]}"
        )
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"malformed chat completion response: {data}") from exc
    if content is None:
        raise ProviderError("chat completion returned empty content")
    return content


def image_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull a JSON object out of model output.

    This is schema-repair plumbing (fenced blocks, surrounding prose), not
    free-text parsing of meaning — meaning always goes through pydantic
    validation afterwards (AGENT.md §28).
    """
    candidates: list[str] = []
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    candidates.extend(m.group(1) for m in _JSON_BLOCK.finditer(text))
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(obj, dict):
            return obj
    raise ProviderError(f"no JSON object found in model output: {last_error}")
