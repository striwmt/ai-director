"""Managed llama.cpp server process, shared by director and vision.

"Optional server process lifecycle" is a runtime-manager responsibility
(AGENT.md §37): the server starts on provider load and stops on unload,
so it never holds VRAM during other phases. If a server is already
healthy on the port (started by the user, Docker, or another provider in
the same phase), it is reused and left running.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ...config import ModelEndpointConfig
from ...errors import ProviderError
from ...logging import get_logger

log = get_logger("provider.llama-server")


class ManagedLlamaServer:
    """Starts/stops one llama-server for a ModelEndpointConfig."""

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        extra = cfg.extra or {}
        # The managed server always runs on localhost; extra.port wins, a
        # configured base_url only contributes its port (config layering can
        # leave a stale base_url from the openai-compatible variant behind).
        port = extra.get("port")
        if port is None and cfg.base_url:
            port = urlparse(cfg.base_url).port
        self.port = int(port or 8102)
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._health_url = f"http://127.0.0.1:{self.port}/health"
        self._process: subprocess.Popen | None = None
        self._log_path: Path | None = None

    def build_command(self) -> list[str]:
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
            "--host", "127.0.0.1", "--port", str(self.port),
            "-ngl", str(extra.get("gpu_layers", 99)),
            "-c", str(self._cfg.context_length or 16384),
            "-fa", "on", "--jinja", "--reasoning-budget", "0",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        ]
        command += [str(a) for a in extra.get("extra_args", [])]
        return command

    async def healthy(self, timeout: float = 2.0) -> bool:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(self._health_url)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_running(self) -> None:
        if await self.healthy():
            log.info("reusing running llama-server on port %d", self.port)
            return
        command = self.build_command()
        log_file = tempfile.NamedTemporaryFile(
            prefix="llama-server-", suffix=".log", delete=False
        )
        self._log_path = Path(log_file.name)
        log.info("starting llama-server: %s (log: %s)",
                 " ".join(command[:4]) + " ...", self._log_path)
        try:
            self._process = subprocess.Popen(
                command, stdout=log_file, stderr=subprocess.STDOUT
            )
        except FileNotFoundError as exc:
            raise ProviderError(
                f"llama-server binary not found: {command[0]} "
                "(install llama.cpp or set the workload's extra.binary)"
            ) from exc
        finally:
            log_file.close()

        timeout = float((self._cfg.extra or {}).get("startup_timeout", 900))
        waited = 0.0
        while not await self.healthy():
            if self._process.poll() is not None:
                raise ProviderError(
                    f"llama-server exited with code {self._process.returncode} "
                    f"(see {self._log_path})"
                )
            if waited >= timeout:
                self.stop()
                raise ProviderError(
                    f"llama-server not healthy after {timeout:.0f}s "
                    f"(see {self._log_path})"
                )
            await asyncio.sleep(2.0)
            waited += 2.0
        log.info("llama-server healthy on port %d", self.port)

    def stop(self) -> None:
        """Stop only what we started; externally managed servers stay up."""
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        log.info("llama-server stopped")
        self._process = None
