"""Color profile detection (AGENT.md §17).

Rule-based detection over probed metadata. Rules come from
config/color_profiles.yaml; each match yields a confidence, low confidence
yields UNKNOWN. Explicit user override always wins (AGENT.md §18).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..logging import get_logger
from ..media.metadata import MediaMetadata
from .profile import ColorProfile, ColorProfileDetection

log = get_logger("color.detect")


class DetectionRule(BaseModel):
    profile: ColorProfile
    confidence: float = 0.5
    transfer: list[str] | None = None
    primaries: list[str] | None = None
    camera_make_contains: list[str] | None = None
    camera_model_contains: list[str] | None = None
    filename_contains: list[str] | None = None

    def matches(self, metadata: MediaMetadata, filename: str) -> bool:
        def norm(value: str | None) -> str:
            return (value or "").lower()

        if self.transfer is not None:
            if norm(metadata.color_transfer) not in [t.lower() for t in self.transfer]:
                return False
        if self.primaries is not None:
            if norm(metadata.color_primaries) not in [p.lower() for p in self.primaries]:
                return False
        if self.camera_make_contains is not None:
            make = norm(metadata.camera_make)
            if not any(s.lower() in make for s in self.camera_make_contains):
                return False
        if self.camera_model_contains is not None:
            model = norm(metadata.camera_model)
            if not any(s.lower() in model for s in self.camera_model_contains):
                return False
        if self.filename_contains is not None:
            name = filename.lower()
            if not any(s.lower() in name for s in self.filename_contains):
                return False
        return True


class ColorProfileDetector:
    def __init__(self, rules: list[DetectionRule], min_confidence: float = 0.5) -> None:
        self.rules = rules
        self.min_confidence = min_confidence

    @classmethod
    def from_yaml(cls, path: Path, min_confidence: float = 0.5) -> "ColorProfileDetector":
        rules: list[DetectionRule] = []
        if path.is_file():
            data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for raw in data.get("detection_rules", []):
                rules.append(DetectionRule.model_validate(raw))
        else:
            log.warning("color profiles file not found: %s (detection limited)", path)
        return cls(rules, min_confidence)

    def detect(
        self,
        metadata: MediaMetadata,
        filename: str,
        override: ColorProfile | None = None,
    ) -> ColorProfileDetection:
        if override is not None and override != ColorProfile.UNKNOWN:
            return ColorProfileDetection(profile=override, confidence=1.0, source="user")

        best: ColorProfileDetection | None = None
        for rule in self.rules:
            if rule.matches(metadata, filename):
                if best is None or rule.confidence > best.confidence:
                    best = ColorProfileDetection(
                        profile=rule.profile, confidence=rule.confidence, source="auto"
                    )

        if best is None or best.confidence < self.min_confidence:
            # UNKNOWN is a legitimate result, not an error (AGENT.md §17).
            return ColorProfileDetection(
                profile=ColorProfile.UNKNOWN,
                confidence=best.confidence if best else 0.0,
                source="auto",
            )
        return best
