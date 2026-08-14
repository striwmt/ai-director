"""Vision (VLM) providers.

Input frames are color-managed analysis representations, never raw Log
(AGENT.md §29). Output is always the VisionAnalysis schema.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import ValidationError

from ...config import ModelEndpointConfig
from ...errors import ProviderError, StructuredOutputError
from ...logging import get_logger
from ..schemas import ImageInput, VisionAnalysis, VisionContext
from ._http import chat_completion, extract_json_object, image_to_data_url, make_client

log = get_logger("provider.vision")

_PROMPT_VERSION = "vision-v1"

_SYSTEM_PROMPT = """You are a video analysis assistant for an AI film editor.
You see representative frames from ONE video segment.
Describe what is actually visible; do not invent details.
Respond with a single JSON object with these fields:
- description: 1-3 sentence factual description of the segment
- subjects: main visible subjects (people, objects, places)
- actions: what is happening
- mood: atmosphere words (e.g. calm, lively, rainy, warm)
- camera_motion: one of static/pan/tilt/handheld/walking/gimbal/unknown
- notable_events: anything noteworthy for an editor (arrival, food served, ...)
- story_roles: possible roles in a story (establishing, detail, transition, action, ending, b-roll)
- narrative_values: why this segment could matter in a travel/vlog story
Return ONLY the JSON object."""


def _build_user_content(images: list[ImageInput], context: VisionContext) -> list[dict]:
    parts: list[dict] = []
    context_lines: list[str] = []
    if context.asset_name:
        context_lines.append(f"Source file: {context.asset_name}")
    if context.duration is not None:
        context_lines.append(f"Segment duration: {context.duration:.1f}s")
    if context.recorded_at:
        context_lines.append(f"Recorded at: {context.recorded_at}")
    if context.transcript_excerpt:
        context_lines.append(f"Speech in segment: {context.transcript_excerpt}")
    for hint in context.hints:
        context_lines.append(f"Hint: {hint}")
    if context_lines:
        parts.append({"type": "text", "text": "\n".join(context_lines)})
    for image in images:
        parts.append(
            {"type": "image_url", "image_url": {"url": image_to_data_url(image.path)}}
        )
    return parts


class OpenAICompatibleVisionProvider:
    """VLM behind an OpenAI-compatible server (vLLM etc.)."""

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._client: httpx.AsyncClient | None = None
        self.name = f"openai-compatible:{cfg.model}"
        self.prompt_version = _PROMPT_VERSION
        self.gpu_resident = False

    async def load(self) -> None:
        self._client = make_client(self._cfg)

    async def unload(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def analyze_segment(
        self, images: list[ImageInput], context: VisionContext
    ) -> VisionAnalysis:
        assert self._client is not None, "provider not loaded"
        if not images:
            raise ProviderError("analyze_segment called with no images")
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_content(images, context)},
        ]
        content = await chat_completion(
            self._client, self._cfg.model, messages, temperature=0.2
        )
        try:
            return VisionAnalysis.model_validate(extract_json_object(content))
        except (ValidationError, ProviderError) as exc:
            raise StructuredOutputError(f"invalid VisionAnalysis: {exc}") from exc


class TransformersVisionProvider:
    """Local VLM via HuggingFace transformers (Qwen3-VL class models).

    All transformers/torch objects stay inside this class.
    """

    def __init__(self, cfg: ModelEndpointConfig) -> None:
        self._cfg = cfg
        self._model: Any = None
        self._processor: Any = None
        self.name = f"transformers:{cfg.model}"
        self.prompt_version = _PROMPT_VERSION

    async def load(self) -> None:
        if self._model is not None:
            return
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        try:
            import torch
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError as exc:
            raise ProviderError(
                "transformers/torch not installed. Install with: uv sync --extra vision"
            ) from exc

        device = self._cfg.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        self._processor = AutoProcessor.from_pretrained(self._cfg.model)
        self._model = AutoModelForImageTextToText.from_pretrained(
            self._cfg.model, dtype=dtype, device_map=device
        )
        self._device = device
        log.info("loaded VLM %s on %s", self._cfg.model, device)

    async def unload(self) -> None:
        self._model = None
        self._processor = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    async def analyze_segment(
        self, images: list[ImageInput], context: VisionContext
    ) -> VisionAnalysis:
        if self._model is None:
            await self.load()
        return await asyncio.to_thread(self._analyze_sync, images, context)

    def _analyze_sync(
        self, images: list[ImageInput], context: VisionContext
    ) -> VisionAnalysis:
        from PIL import Image as PILImage

        pil_images = [PILImage.open(i.path).convert("RGB") for i in images]
        content: list[dict] = [{"type": "image"} for _ in pil_images]
        text_parts = [_SYSTEM_PROMPT]
        if context.transcript_excerpt:
            text_parts.append(f"Speech in segment: {context.transcript_excerpt}")
        if context.duration is not None:
            text_parts.append(f"Segment duration: {context.duration:.1f}s")
        content.append({"type": "text", "text": "\n".join(text_parts)})

        messages = [{"role": "user", "content": content}]
        prompt = self._processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        inputs = self._processor(
            text=[prompt], images=pil_images, return_tensors="pt"
        ).to(self._model.device)
        output_ids = self._model.generate(**inputs, max_new_tokens=512, do_sample=False)
        generated = output_ids[:, inputs["input_ids"].shape[1]:]
        text = self._processor.batch_decode(generated, skip_special_tokens=True)[0]
        try:
            return VisionAnalysis.model_validate(extract_json_object(text))
        except (ValidationError, ProviderError):
            # Salvage: keep the raw description rather than losing the analysis.
            log.warning("VLM output not valid JSON; storing as plain description")
            return VisionAnalysis(description=text.strip()[:2000])
