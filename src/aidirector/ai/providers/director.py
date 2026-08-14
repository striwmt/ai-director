"""Director (LLM) providers."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlparse

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
        self._cfg = cfg
        extra = cfg.extra or {}
        # The managed server always runs on localhost; extra.port wins, a
        # configured base_url only contributes its port (config layering can
        # leave a stale base_url from the openai-compatible variant behind).
        port = extra.get("port")
        if port is None and cfg.base_url:
            port = urlparse(cfg.base_url).port
        self._port = int(port or 8102)
        base_url = f"http://127.0.0.1:{self._port}/v1"
        self._health_url = f"http://127.0.0.1:{self._port}/health"

        inner_cfg = cfg.model_copy(
            update={"provider": "openai-compatible", "base_url": base_url}
        )
        self._inner = OpenAICompatibleDirectorProvider(inner_cfg)
        self._process: subprocess.Popen | None = None
        self._log_path: Path | None = None
        self.name = f"llama-server:{cfg.model}"
        # Separate process; VRAM timing is handled by start/stop lifecycle,
        # so it must not evict in-process models (embedding runs alongside).
        self.gpu_resident = False

    def _build_command(self) -> list[str]:
        extra = self._cfg.extra or {}
        binary = str(extra.get("binary", "llama-server"))
        if extra.get("args"):
            return [binary, *(str(a) for a in extra["args"])]
        model_flag = (
            ["-m", self._cfg.model]
            if self._cfg.model.endswith(".gguf")
            else ["-hf", self._cfg.model]
        )
        command = [
            binary, *model_flag,
            "--host", "127.0.0.1", "--port", str(self._port),
            "-ngl", str(extra.get("gpu_layers", 99)),
            "-c", str(self._cfg.context_length or 16384),
            "-fa", "on", "--jinja", "--reasoning-budget", "0",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        ]
        command += [str(a) for a in extra.get("extra_args", [])]
        return command

    async def _healthy(self, timeout: float = 2.0) -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(self._health_url)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def load(self) -> None:
        if not await self._healthy():
            command = self._build_command()
            log_file = tempfile.NamedTemporaryFile(
                prefix="llama-server-", suffix=".log", delete=False
            )
            self._log_path = Path(log_file.name)
            log.info("starting director server: %s (log: %s)",
                     " ".join(command[:4]) + " ...", self._log_path)
            try:
                self._process = subprocess.Popen(
                    command, stdout=log_file, stderr=subprocess.STDOUT
                )
            except FileNotFoundError as exc:
                raise ProviderError(
                    f"llama-server binary not found: {command[0]} "
                    "(install llama.cpp or set models.director.extra.binary)"
                ) from exc
            finally:
                log_file.close()

            timeout = float((self._cfg.extra or {}).get("startup_timeout", 900))
            waited = 0.0
            while not await self._healthy():
                if self._process.poll() is not None:
                    raise ProviderError(
                        f"llama-server exited with code {self._process.returncode} "
                        f"(see {self._log_path})"
                    )
                if waited >= timeout:
                    self._stop_process()
                    raise ProviderError(
                        f"llama-server not healthy after {timeout:.0f}s "
                        f"(see {self._log_path})"
                    )
                await asyncio.sleep(2.0)
                waited += 2.0
            log.info("director server healthy on port %d", self._port)
        else:
            log.info("reusing running director server on port %d", self._port)
        await self._inner.load()

    def _stop_process(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        log.info("director server stopped")
        self._process = None

    async def unload(self) -> None:
        await self._inner.unload()
        # Only stop what we started; externally managed servers stay up.
        self._stop_process()

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
