"""Music analysis providers: CLAP audio-text embeddings and an audio LLM.

CLAP maps audio and tag texts into one space (zero-shot mood/genre tags,
similarity search). The audio LLM (Qwen2.5-Omni family, text output only)
writes a free-text description of a track. Both follow phase execution:
loaded on demand, released before the director runs.
"""

from __future__ import annotations

import asyncio
import gc
from pathlib import Path
from typing import Any

from ...config import ModelEndpointConfig
from ...errors import ProviderError
from ...logging import get_logger
from ..schemas import Embedding

log = get_logger("provider.music")

_INSTALL_HINT = "Install with: uv sync --extra music"

# CLAP (HTSAT, unfused) consumes 10-second windows at 48 kHz.
_CLAP_SAMPLE_RATE = 48000
_CLAP_WINDOW_SECONDS = 10.0
_CLAP_WINDOW_POSITIONS = (0.25, 0.5, 0.75)


def _feature_tensor(output: Any) -> Any:
    """get_text/audio_features returns a bare tensor on transformers 4.x
    and an output object with pooler_output (the projected embedding) on
    5.x — normalize to the tensor."""
    pooled = getattr(output, "pooler_output", None)
    return pooled if pooled is not None else output


def _read_wav(path: Path) -> tuple[Any, int]:
    try:
        import soundfile as sf
    except ImportError as exc:
        raise ProviderError(f"soundfile not installed. {_INSTALL_HINT}") from exc
    data, rate = sf.read(str(path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, rate


class ClapMusicEmbeddingProvider:
    """laion/clap-htsat-* via transformers ClapModel."""

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._model: Any = None
        self._processor: Any = None
        self._device = "cpu"
        self.name = f"clap:{cfg.model}"

    async def load(self) -> None:
        if self._model is not None:
            return
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        try:
            import torch
            from transformers import ClapModel, ClapProcessor
        except ImportError as exc:
            raise ProviderError(
                f"transformers/torch not installed. {_INSTALL_HINT}"
            ) from exc
        device = self._cfg.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._processor = ClapProcessor.from_pretrained(self._cfg.model)
        self._model = ClapModel.from_pretrained(self._cfg.model).to(device).eval()
        self._device = device
        log.info("loaded CLAP model %s on %s", self._cfg.model, device)

    async def unload(self) -> None:
        self._model = None
        self._processor = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def _embed_audio_sync(self, wav: Path) -> Embedding:
        import numpy as np
        import torch

        data, rate = _read_wav(wav)
        if rate != _CLAP_SAMPLE_RATE:
            raise ProviderError(
                f"CLAP expects {_CLAP_SAMPLE_RATE} Hz input, got {rate} ({wav})"
            )
        window = int(_CLAP_WINDOW_SECONDS * rate)
        clips = []
        for position in _CLAP_WINDOW_POSITIONS:
            start = max(0, int(len(data) * position) - window // 2)
            chunk = data[start:start + window]
            if len(chunk) >= rate:  # ignore sub-second tails
                clips.append(chunk)
        if not clips:
            clips = [data]
        vectors = []
        with torch.no_grad():
            for chunk in clips:
                inputs = self._processor(
                    audio=chunk, sampling_rate=rate, return_tensors="pt"
                ).to(self._device)
                feats = _feature_tensor(self._model.get_audio_features(**inputs))[0]
                feats = feats / feats.norm()
                vectors.append(feats.cpu().numpy())
        mean = np.mean(vectors, axis=0)
        mean = mean / (np.linalg.norm(mean) or 1.0)
        return Embedding(vector=[float(x) for x in mean], model=self._cfg.model)

    async def embed_audio(self, wav: Path) -> Embedding:
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._embed_audio_sync, wav)

    def _embed_text_sync(self, texts: list[str]) -> list[Embedding]:
        import torch

        with torch.no_grad():
            inputs = self._processor(
                text=texts, return_tensors="pt", padding=True
            ).to(self._device)
            feats = _feature_tensor(self._model.get_text_features(**inputs))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return [
            Embedding(vector=[float(x) for x in row], model=self._cfg.model)
            for row in feats.cpu().numpy()
        ]

    async def embed_music_text(self, texts: list[str]) -> list[Embedding]:
        if not texts:
            return []
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._embed_text_sync, texts)


def clap_text_embedding_cpu(model_name: str, text: str) -> list[float]:
    """One-off CLAP text embedding on the CPU (no GPU contention).

    Used at director time to rank a >60-track library against the story
    while the director LLM may already occupy the GPU.
    """
    import torch
    from transformers import ClapProcessor, ClapTextModelWithProjection

    processor = ClapProcessor.from_pretrained(model_name)
    model = ClapTextModelWithProjection.from_pretrained(model_name).eval()
    with torch.no_grad():
        inputs = processor(text=[text], return_tensors="pt", padding=True)
        output = model(**inputs)
        feats = getattr(output, "text_embeds", None)
        if feats is None:
            feats = _feature_tensor(output)
        feats = feats[0]
        feats = feats / feats.norm()
    return [float(x) for x in feats.numpy()]


class QwenOmniMusicProvider:
    """Audio-capable LLM (Qwen2.5-Omni family), text output only.

    Defaults to the Thinker-only class so the Talker/token2wav weights are
    never loaded; `extra.model_class` swaps in other classes (future
    Qwen3-Omni included) without code changes.
    """

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._model: Any = None
        self._processor: Any = None
        self.name = f"omni:{cfg.model}"

    async def load(self) -> None:
        if self._model is not None:
            return
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        try:
            import torch
            import transformers
            from transformers import AutoProcessor
        except ImportError as exc:
            raise ProviderError(
                f"transformers/torch not installed. {_INSTALL_HINT}"
            ) from exc

        class_name = self._cfg.extra.get(
            "model_class", "Qwen2_5OmniThinkerForConditionalGeneration"
        )
        model_class = getattr(transformers, class_name, None)
        if model_class is None:
            raise ProviderError(
                f"transformers has no class {class_name!r} "
                "(set models.music_understanding.extra.model_class)"
            )

        kwargs: dict[str, Any] = {"dtype": torch.float16, "device_map": "auto"}
        if self._cfg.extra.get("quantization") == "4bit":
            try:
                from transformers import BitsAndBytesConfig

                kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.float16,
                )
                kwargs.pop("dtype")
            except ImportError as exc:
                raise ProviderError(
                    f"bitsandbytes not installed. {_INSTALL_HINT}"
                ) from exc
        if class_name == "Qwen2_5OmniForConditionalGeneration":
            kwargs["enable_audio_output"] = False

        self._processor = AutoProcessor.from_pretrained(self._cfg.model)
        self._model = model_class.from_pretrained(self._cfg.model, **kwargs).eval()
        if hasattr(self._model, "disable_talker"):
            self._model.disable_talker()
        log.info("loaded audio LLM %s (%s)", self._cfg.model, class_name)

    async def unload(self) -> None:
        self._model = None
        self._processor = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def _describe_sync(self, wav: Path, prompt: str) -> str:
        data, rate = _read_wav(wav)
        expected = self._processor.feature_extractor.sampling_rate
        if rate != expected:
            raise ProviderError(
                f"audio LLM expects {expected} Hz input, got {rate} ({wav})"
            )
        conversation = [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": "placeholder"},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        text = self._processor.apply_chat_template(
            conversation, add_generation_prompt=True, tokenize=False
        )
        inputs = self._processor(
            text=text, audio=[data], sampling_rate=rate,
            return_tensors="pt", padding=True,
        ).to(self._model.device)
        generate_kwargs: dict[str, Any] = {"max_new_tokens": 220}
        if hasattr(self._model, "talker"):
            generate_kwargs["return_audio"] = False
        output = self._model.generate(**inputs, **generate_kwargs)
        if isinstance(output, tuple):
            output = output[0]
        new_tokens = output[:, inputs["input_ids"].shape[1]:]
        decoded = self._processor.batch_decode(
            new_tokens, skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        return decoded.strip()

    async def describe_audio(self, wav: Path, prompt: str) -> str:
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._describe_sync, wav, prompt)
