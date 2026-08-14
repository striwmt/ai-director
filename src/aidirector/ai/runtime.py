"""Model runtime manager.

Single-GPU (e.g. RTX 5060 Ti 16GB) is the first-class use case: models are
loaded in phases, never all resident at once (AGENT.md §36-§38, §80).

The manager owns provider lifecycle (load/unload, reuse, cleanup) and knows
nothing about story logic. Provider implementations are imported lazily so
that importing this package never loads a model (AGENT.md §75).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Literal

from ..config import ModelEndpointConfig, ModelsConfig
from ..errors import ProviderError
from ..logging import get_logger

log = get_logger("runtime")

Workload = Literal["vision", "director", "speech", "embedding"]

_FACTORY = Callable[[ModelEndpointConfig], Any]


def _make_speech_provider(cfg: ModelEndpointConfig) -> Any:
    if cfg.provider == "faster-whisper":
        from .providers.speech import FasterWhisperProvider

        return FasterWhisperProvider(cfg)
    raise ProviderError(f"unknown speech provider: {cfg.provider}")


def _make_vision_provider(cfg: ModelEndpointConfig) -> Any:
    if cfg.provider == "openai-compatible":
        from .providers.vision import OpenAICompatibleVisionProvider

        return OpenAICompatibleVisionProvider(cfg)
    if cfg.provider == "transformers":
        from .providers.vision import TransformersVisionProvider

        return TransformersVisionProvider(cfg)
    raise ProviderError(f"unknown vision provider: {cfg.provider}")


def _make_director_provider(cfg: ModelEndpointConfig) -> Any:
    if cfg.provider == "openai-compatible":
        from .providers.director import OpenAICompatibleDirectorProvider

        return OpenAICompatibleDirectorProvider(cfg)
    if cfg.provider == "llama-server":
        from .providers.director import LlamaServerDirectorProvider

        return LlamaServerDirectorProvider(cfg)
    raise ProviderError(f"unknown director provider: {cfg.provider}")


def _make_embedding_provider(cfg: ModelEndpointConfig) -> Any:
    if cfg.provider == "openai-compatible":
        from .providers.embedding import HttpEmbeddingProvider

        return HttpEmbeddingProvider(cfg)
    if cfg.provider == "sentence-transformers":
        from .providers.embedding import SentenceTransformersEmbeddingProvider

        return SentenceTransformersEmbeddingProvider(cfg)
    if cfg.provider == "transformers":
        from .providers.embedding import TransformersEmbeddingProvider

        return TransformersEmbeddingProvider(cfg)
    raise ProviderError(f"unknown embedding provider: {cfg.provider}")


_FACTORIES: dict[Workload, _FACTORY] = {
    "speech": _make_speech_provider,
    "vision": _make_vision_provider,
    "director": _make_director_provider,
    "embedding": _make_embedding_provider,
}


class ModelRuntimeManager:
    """Owns which model occupies the GPU at any moment.

    ``exclusive=True`` (default) unloads every other workload before loading
    a new one — phase execution for single-GPU machines.
    """

    def __init__(self, models: ModelsConfig, *, exclusive: bool = True) -> None:
        self._models = models
        self._exclusive = exclusive
        self._active: dict[Workload, Any] = {}
        # Test/embedding-in-python overrides: inject a prebuilt provider.
        self._overrides: dict[Workload, Any] = {}

    def override(self, workload: Workload, provider: Any) -> None:
        """Inject a provider instance (used by tests and custom wiring)."""
        self._overrides[workload] = provider

    def provider_label(self, workload: Workload) -> str:
        """Provider identity string WITHOUT loading the model.

        Used for cache lookups (e.g. which embeddings already exist) so a
        fully-cached phase never touches the GPU (AGENT.md §45).
        """
        if workload in self._active:
            return getattr(self._active[workload], "name", "unknown")
        if workload in self._overrides:
            return getattr(self._overrides[workload], "name", "unknown")
        # Constructing a provider is cheap; heavy imports happen in load().
        provider = _FACTORIES[workload](self._config_for(workload))
        return getattr(provider, "name", "unknown")

    def _config_for(self, workload: Workload) -> ModelEndpointConfig:
        return getattr(self._models, workload)

    async def acquire(self, workload: Workload) -> Any:
        if workload in self._active:
            return self._active[workload]

        if workload in self._overrides:
            provider = self._overrides[workload]
        else:
            cfg = self._config_for(workload)
            provider = _FACTORIES[workload](cfg)

        # Phase execution: a GPU-resident model evicts other GPU-resident
        # models. Remote/HTTP providers occupy no VRAM — they neither evict
        # nor get evicted (so a llama-server director coexists with a local
        # embedding model).
        if self._exclusive and getattr(provider, "gpu_resident", True):
            for other in list(self._active):
                if other == workload:
                    continue
                if getattr(self._active[other], "gpu_resident", True):
                    await self.release(other)

        log.info("loading %s provider: %s", workload, getattr(provider, "name", "?"))
        try:
            await provider.load()
        except Exception:
            # Cleanup on error: never leave a half-loaded model registered.
            try:
                await provider.unload()
            except Exception:
                pass
            raise
        self._active[workload] = provider
        return provider

    async def release(self, workload: Workload) -> None:
        provider = self._active.pop(workload, None)
        if provider is None:
            return
        log.info("unloading %s provider: %s", workload, getattr(provider, "name", "?"))
        try:
            await provider.unload()
        except Exception as exc:
            log.warning("error unloading %s provider: %s", workload, exc)

    async def release_all(self) -> None:
        for workload in list(self._active):
            await self.release(workload)

    @asynccontextmanager
    async def use(self, workload: Workload) -> AsyncIterator[Any]:
        provider = await self.acquire(workload)
        try:
            yield provider
        finally:
            # Keep the provider resident for reuse within a phase; callers
            # doing phase switches rely on acquire()'s exclusive eviction.
            pass
