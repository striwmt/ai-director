"""Color transform registry (AGENT.md §15/§16)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..logging import get_logger
from .lut import lut_available
from .profile import ColorProfile
from .transforms import ColorTransform

log = get_logger("color.registry")


class ColorTransformRegistry:
    def __init__(self, transforms: list[ColorTransform], luts_dir: Path | None = None) -> None:
        self._transforms = transforms
        self.luts_dir = luts_dir

    @classmethod
    def from_yaml(
        cls, path: Path, *, luts_dir: Path | None = None, base_dir: Path | None = None
    ) -> "ColorTransformRegistry":
        transforms: list[ColorTransform] = []
        if path.is_file():
            data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for raw in data.get("transforms", []):
                lut_path = raw.get("path")
                resolved: Path | None = None
                if lut_path:
                    resolved = Path(lut_path)
                    if base_dir is not None and not resolved.is_absolute():
                        resolved = base_dir / resolved
                transforms.append(
                    ColorTransform(
                        id=raw["id"],
                        source_profile=ColorProfile(raw["source"]),
                        destination_profile=ColorProfile(raw["destination"]),
                        type=raw.get("type", "lut3d"),
                        path=resolved,
                        vendor=raw.get("vendor"),
                        version=raw.get("version"),
                        filter_expr=raw.get("filter"),
                        purposes=tuple(raw.get("purposes", ["analysis", "preview"])),
                    )
                )
        else:
            log.warning("color profiles file not found: %s (no transforms loaded)", path)
        return cls(transforms, luts_dir)

    def resolve(
        self,
        source: ColorProfile,
        destination: ColorProfile,
        purpose: str,
    ) -> ColorTransform | None:
        """Return a usable transform, or None.

        A lut3d transform whose LUT file is absent is skipped (the user has
        not installed the vendor LUT); callers decide the fallback.
        """
        for transform in self._transforms:
            if transform.source_profile != source:
                continue
            if transform.destination_profile != destination:
                continue
            if not transform.supports(purpose):
                continue
            if transform.type == "lut3d" and not lut_available(transform.path):
                log.debug("transform %s skipped: LUT missing at %s", transform.id, transform.path)
                continue
            return transform
        if source == destination:
            return ColorTransform(
                id=f"{source.value}_identity",
                source_profile=source,
                destination_profile=destination,
                type="passthrough",
            )
        return None

    def all_transforms(self) -> list[ColorTransform]:
        return list(self._transforms)
