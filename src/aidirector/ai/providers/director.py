"""Director (LLM) providers."""

from __future__ import annotations

import asyncio
import gc
import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ...config import ModelEndpointConfig
from ...errors import ProviderError, StructuredOutputError
from ...logging import get_logger
from ..schemas import Message
from ._http import chat_completion, extract_json_object, make_client

log = get_logger("provider.director")

T = TypeVar("T", bound=BaseModel)

_MAX_ATTEMPTS = 3


def _chat_with_schema(messages: list[Message], schema_text: str) -> list[dict]:
    """Fold the JSON-schema instruction into the LEADING system message.

    Strict chat templates (Qwen3.8+) reject system messages anywhere but
    the start of the conversation.
    """
    instruction = (
        "Respond with a single JSON object matching this JSON schema, "
        "with no extra commentary:\n" + schema_text
    )
    chat: list[dict] = [{"role": m.role, "content": m.content} for m in messages]
    if chat and chat[0]["role"] == "system":
        chat[0] = {
            "role": "system",
            "content": f"{chat[0]['content']}\n\n{instruction}",
        }
    else:
        chat.insert(0, {"role": "system", "content": instruction})
    return chat


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
        chat = _chat_with_schema(messages, schema_text)

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


def _strip_thinking(text: str) -> str:
    """Drop a leading <think>...</think> block (Qwen3-style models)."""
    marker = "</think>"
    index = text.rfind(marker)
    return text[index + len(marker):] if index != -1 else text


class TransformersDirectorProvider:
    """In-process director LLM via HuggingFace transformers.

    Loads exactly like the vision/music models: weights auto-download to
    the HF cache, the model lives in this process and is phase-evicted by
    the runtime manager — no external server or binary required.
    ``extra.quantization: 4bit`` (bitsandbytes NF4) fits an 8B model in a
    16 GB GPU. Slower at generation than llama.cpp on the same hardware;
    ``provider: llama-server`` remains the faster option.
    """

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._model: Any = None
        self._tokenizer: Any = None
        self.name = f"transformers:{cfg.model}"

    async def load(self) -> None:
        if self._model is not None:
            return
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise ProviderError(
                "transformers/torch not installed. Install with: uv sync --extra vision"
            ) from exc

        device = self._cfg.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        kwargs: dict[str, Any] = {
            "dtype": torch.bfloat16 if device == "cuda" else torch.float32,
            "device_map": device,
        }
        if self._cfg.extra.get("quantization") == "4bit" and device == "cuda":
            try:
                from transformers import BitsAndBytesConfig

                kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                )
                kwargs.pop("dtype")
            except ImportError as exc:
                raise ProviderError(
                    "bitsandbytes not installed. Install with: uv sync --extra vision"
                ) from exc
        self._tokenizer = AutoTokenizer.from_pretrained(self._cfg.model)
        self._model = AutoModelForCausalLM.from_pretrained(
            self._cfg.model, **kwargs
        ).eval()
        log.info("loaded director LLM %s on %s", self._cfg.model, device)

    async def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def _generate_sync(self, chat: list[dict], thinking: bool) -> str:
        import torch

        text = self._tokenizer.apply_chat_template(
            chat, add_generation_prompt=True, tokenize=False,
            enable_thinking=thinking,
        )
        inputs = self._tokenizer([text], return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=int(self._cfg.extra.get("max_new_tokens", 4096)),
                do_sample=True, temperature=0.4, top_p=0.9,
            )
        new_tokens = output[:, inputs["input_ids"].shape[1]:]
        decoded = self._tokenizer.batch_decode(
            new_tokens, skip_special_tokens=True
        )[0]
        return _strip_thinking(decoded).strip()

    async def generate_structured(
        self,
        messages: list[Message],
        response_model: type[T],
        *,
        thinking: bool | None = None,
    ) -> T:
        if self._model is None:
            await self.load()
        schema_text = json.dumps(
            response_model.model_json_schema(), ensure_ascii=False
        )
        chat = _chat_with_schema(messages, schema_text)

        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            content = await asyncio.to_thread(
                self._generate_sync, chat, bool(thinking)
            )
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


class LlamaServerDirectorProvider:
    """Owns a llama.cpp server process and talks to it OpenAI-compatibly.

    "Optional server process lifecycle" is a runtime-manager responsibility
    (AGENT.md §37): the server is started on load and stopped on unload, so
    it never holds VRAM during the vision/speech analysis phases. If a
    server is already healthy on the port (started by the user or Docker),
    it is reused and left running.

    Config (models.yaml):
        director:
          provider: llama-server
          model: Qwen/Qwen3-8B-GGUF:Q4_K_M      # -hf ref, or a local .gguf path
          context_length: 16384
          extra:
            binary: llama-server                 # executable name or path
            port: 8102
            startup_timeout: 900                 # first run downloads the GGUF
            gpu_layers: 99
            extra_args: []                       # appended to the command
            args: []                             # full override of all args
    """

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        from .llama_server import ManagedLlamaServer

        self._cfg = cfg
        self._server = ManagedLlamaServer(cfg)
        inner_cfg = cfg.model_copy(
            update={"provider": "openai-compatible",
                    "base_url": self._server.base_url}
        )
        self._inner = OpenAICompatibleDirectorProvider(inner_cfg)
        self.name = f"llama-server:{cfg.model}"
        # Separate process; VRAM timing is handled by start/stop lifecycle,
        # so it must not evict in-process models (embedding runs alongside).
        self.gpu_resident = False

    # Exposed for tests and diagnostics.
    @property
    def _process(self):
        return self._server._process

    def _build_command(self) -> list[str]:
        return self._server.build_command()

    async def load(self) -> None:
        await self._server.ensure_running()
        await self._inner.load()

    async def unload(self) -> None:
        await self._inner.unload()
        self._server.stop()

    async def generate_structured(
        self,
        messages: list[Message],
        response_model: type[T],
        *,
        thinking: bool | None = None,
    ) -> T:
        return await self._inner.generate_structured(
            messages, response_model, thinking=thinking
        )
