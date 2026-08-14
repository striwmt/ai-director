"""AI Services facade (AGENT.md §24).

Business logic calls task-oriented methods here; the facade hides provider
selection, runtime acquisition and (via providers) schema validation and
retries. It never leaks model-library objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..logging import get_logger
from .runtime import ModelRuntimeManager
from .schemas import (
    Embedding,
    ImageInput,
    Message,
    Transcript,
    TranscriptionOptions,
    VisionAnalysis,
    VisionContext,
)

log = get_logger("ai.services")

T = TypeVar("T", bound=BaseModel)


class AIServices:
    def __init__(self, runtime: ModelRuntimeManager) -> None:
        self.runtime = runtime

    # -- speech --------------------------------------------------------

    async def transcribe(
        self, audio: Path, options: TranscriptionOptions | None = None
    ) -> Transcript:
        provider = await self.runtime.acquire("speech")
        return await provider.transcribe(audio, options or TranscriptionOptions())

    # -- vision --------------------------------------------------------

    async def understand_segment(
        self, images: list[ImageInput], context: VisionContext
    ) -> VisionAnalysis:
        provider = await self.runtime.acquire("vision")
        return await provider.analyze_segment(images, context)

    # -- embedding -----------------------------------------------------

    async def embed_text(self, texts: list[str]) -> list[Embedding]:
        provider = await self.runtime.acquire("embedding")
        return await provider.embed_text(texts)

    # -- director ------------------------------------------------------

    async def generate_structured(
        self,
        messages: list[Message],
        response_model: type[T],
        *,
        thinking: bool | None = None,
    ) -> T:
        provider = await self.runtime.acquire("director")
        return await provider.generate_structured(
            messages, response_model, thinking=thinking
        )

    # -- provenance helpers ---------------------------------------------

    def provider_name(self, workload: str) -> str:
        """Best-effort provider identity for provenance records."""
        provider = self.runtime._active.get(workload)  # noqa: SLF001
        return getattr(provider, "name", "unknown") if provider else "unknown"
