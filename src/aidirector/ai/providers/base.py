"""Provider protocols — the contract between business logic and AI backends.

Business logic (director/, perception/) only sees these protocols and the
schemas in ``ai.schemas``. Model libraries stay inside implementations
(AGENT.md §22-§35, §76, §77).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from ..schemas import (
    Embedding,
    ImageInput,
    Message,
    Transcript,
    TranscriptionOptions,
    VisionAnalysis,
    VisionContext,
)

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class BaseProvider(Protocol):
    """Lifecycle shared by all providers, driven by the runtime manager."""

    name: str

    async def load(self) -> None: ...

    async def unload(self) -> None: ...


class DirectorProvider(Protocol):
    name: str

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def generate_structured(
        self,
        messages: list[Message],
        response_model: type[T],
        *,
        thinking: bool | None = None,
    ) -> T: ...


class VisionProvider(Protocol):
    name: str

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def analyze_segment(
        self,
        images: list[ImageInput],
        context: VisionContext,
    ) -> VisionAnalysis: ...


class SpeechProvider(Protocol):
    name: str

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def transcribe(
        self,
        audio: Path,
        options: TranscriptionOptions,
    ) -> Transcript: ...


class EmbeddingProvider(Protocol):
    name: str

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def embed_text(
        self, texts: list[str], prompt_name: str | None = None
    ) -> list[Embedding]: ...

    async def embed_images(self, images: list[Path]) -> list[Embedding]: ...


class MusicEmbeddingProvider(Protocol):
    """Audio-text embeddings in one space (CLAP-style)."""

    name: str

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def embed_audio(self, wav: Path) -> Embedding: ...

    async def embed_music_text(self, texts: list[str]) -> list[Embedding]: ...


class MusicUnderstandingProvider(Protocol):
    """Audio-capable LLM describing a track in natural language."""

    name: str

    async def load(self) -> None: ...
    async def unload(self) -> None: ...

    async def describe_audio(self, wav: Path, prompt: str) -> str: ...
