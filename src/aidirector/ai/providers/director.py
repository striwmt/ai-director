"""Director (LLM) providers."""

from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ...config import ModelEndpointConfig
from ...errors import StructuredOutputError
from ...logging import get_logger
from ..schemas import Message
from ._http import chat_completion, extract_json_object, make_client

log = get_logger("provider.director")

T = TypeVar("T", bound=BaseModel)

_MAX_ATTEMPTS = 3


class OpenAICompatibleDirectorProvider:
    """Structured generation over POST /v1/chat/completions.

    Flow (AGENT.md §28): send schema -> parse -> pydantic validation ->
    repair/retry with the validation error fed back to the model.
    """

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._client: httpx.AsyncClient | None = None
        self.name = f"openai-compatible:{cfg.model}"
        self.gpu_resident = False  # remote server owns the VRAM

    async def load(self) -> None:
        self._client = make_client(self._cfg)

    async def unload(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def generate_structured(
        self,
        messages: list[Message],
        response_model: type[T],
        *,
        thinking: bool | None = None,
    ) -> T:
        assert self._client is not None, "provider not loaded"
        schema = response_model.model_json_schema()
        schema_text = json.dumps(schema, ensure_ascii=False)

        chat: list[dict] = [{"role": m.role, "content": m.content} for m in messages]
        chat.append(
            {
                "role": "system",
                "content": (
                    "Respond with a single JSON object matching this JSON schema, "
                    "with no extra commentary:\n" + schema_text
                ),
            }
        )

        response_format = {
            "type": "json_schema",
            "json_schema": {"name": response_model.__name__, "schema": schema},
        }

        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                content = await chat_completion(
                    self._client,
                    self._cfg.model,
                    chat,
                    response_format=response_format if attempt == 1 else None,
                )
            except Exception as exc:
                # Some servers reject json_schema response_format; retry
                # without it before giving up.
                if attempt == 1:
                    log.debug("json_schema response_format failed (%s); retrying plain", exc)
                    last_error = exc
                    continue
                raise

            try:
                data = extract_json_object(content)
                return response_model.model_validate(data)
            except (ValidationError, Exception) as exc:
                last_error = exc
                log.warning(
                    "structured output invalid (attempt %d/%d): %s",
                    attempt, _MAX_ATTEMPTS, exc,
                )
                chat.append({"role": "assistant", "content": content})
                chat.append(
                    {
                        "role": "user",
                        "content": (
                            "The previous response failed validation with this error:\n"
                            f"{exc}\n"
                            "Return ONLY the corrected JSON object."
                        ),
                    }
                )

        raise StructuredOutputError(
            f"failed to produce valid {response_model.__name__} "
            f"after {_MAX_ATTEMPTS} attempts: {last_error}"
        )
