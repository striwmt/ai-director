"""Embedding providers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from ...config import ModelEndpointConfig
from ...errors import ProviderError
from ...logging import get_logger
from ..schemas import Embedding
from ._http import make_client

log = get_logger("provider.embedding")


class HttpEmbeddingProvider:
    """POST /v1/embeddings against an OpenAI-compatible server."""

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._client: httpx.AsyncClient | None = None
        self.name = f"openai-compatible:{cfg.model}"
        self.gpu_resident = False

    async def load(self) -> None:
        self._client = make_client(self._cfg)

    async def unload(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def embed_text(self, texts: list[str]) -> list[Embedding]:
        assert self._client is not None, "provider not loaded"
        if not texts:
            return []
        try:
            response = await self._client.post(
                "/embeddings", json={"model": self._cfg.model, "input": texts}
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"embedding request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"embedding HTTP {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        try:
            rows = sorted(data["data"], key=lambda r: r["index"])
            return [
                Embedding(vector=row["embedding"], model=self._cfg.model) for row in rows
            ]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"malformed embedding response: {exc}") from exc

    async def embed_images(self, images: list[Path]) -> list[Embedding]:
        raise ProviderError(
            "HttpEmbeddingProvider does not support image embedding; "
            "use the transformers embedding provider"
        )


class SentenceTransformersEmbeddingProvider:
    """Multimodal embeddings via sentence-transformers.

    The official integration path for Qwen3-VL-Embedding models: text,
    image paths, and text+image inputs share one embedding space.
    """

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._model: Any = None
        self.name = f"sentence-transformers:{cfg.model}"

    async def load(self) -> None:
        if self._model is not None:
            return
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ProviderError(
                "sentence-transformers not installed. "
                "Install with: uv sync --extra embedding"
            ) from exc
        device = self._cfg.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        kwargs: dict[str, Any] = dict(self._cfg.extra)
        self._model = SentenceTransformer(self._cfg.model, device=device, **kwargs)
        self._device = device
        log.info("loaded embedding model %s on %s", self._cfg.model, device)

    async def unload(self) -> None:
        from ._cuda import free_cuda_memory

        self._model = None
        free_cuda_memory()

    def _encode(self, inputs: list[Any]) -> list[Embedding]:
        vectors = self._model.encode(inputs, normalize_embeddings=True)
        return [
            Embedding(vector=[float(x) for x in vec], model=self._cfg.model)
            for vec in vectors
        ]

    async def embed_text(self, texts: list[str]) -> list[Embedding]:
        if not texts:
            return []
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._encode, texts)

    async def embed_images(self, images: list[Path]) -> list[Embedding]:
        if not images:
            return []
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._encode, [str(p) for p in images])


class TransformersEmbeddingProvider:
    """Local text/image embeddings via HuggingFace transformers."""

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
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise ProviderError(
                "transformers/torch not installed. Install with: uv sync --extra embedding"
            ) from exc
        device = self._cfg.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._tokenizer = AutoTokenizer.from_pretrained(self._cfg.model)
        self._model = AutoModel.from_pretrained(self._cfg.model).to(device).eval()
        self._device = device
        log.info("loaded embedding model %s on %s", self._cfg.model, device)

    async def unload(self) -> None:
        from ._cuda import free_cuda_memory

        self._model = None
        self._tokenizer = None
        free_cuda_memory()

    async def embed_text(self, texts: list[str]) -> list[Embedding]:
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._embed_text_sync, texts)

    def _embed_text_sync(self, texts: list[str]) -> list[Embedding]:
        import torch

        if not texts:
            return []
        with torch.no_grad():
            batch = self._tokenizer(
                texts, padding=True, truncation=True, max_length=512, return_tensors="pt"
            ).to(self._device)
            output = self._model(**batch)
            hidden = output.last_hidden_state  # (B, T, H)
            mask = batch["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, dim=-1)
        return [
            Embedding(vector=vec.tolist(), model=self._cfg.model)
            for vec in pooled.cpu()
        ]

    async def embed_images(self, images: list[Path]) -> list[Embedding]:
        raise ProviderError(
            f"image embedding is not implemented for {self._cfg.model}; "
            "configure a multimodal embedding provider"
        )
