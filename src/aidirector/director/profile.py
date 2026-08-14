"""Director profile loading (AGENT.md §62)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ..errors import ConfigError


class DirectorProfile(BaseModel):
    name: str
    description: str = ""
    goals: list[str] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)
    avoid: list[str] = Field(default_factory=list)

    def to_prompt_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(), allow_unicode=True, sort_keys=False
        )


def load_director_profile(profiles_dir: Path, name: str) -> DirectorProfile:
    path = profiles_dir / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in profiles_dir.glob("*.yaml")) if profiles_dir.is_dir() else []
        raise ConfigError(
            f"director profile '{name}' not found in {profiles_dir} "
            f"(available: {', '.join(available) or 'none'})"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("name", name)
    # preferences values may be non-strings in YAML; normalize.
    prefs = data.get("preferences") or {}
    data["preferences"] = {str(k): str(v) for k, v in prefs.items()}
    return DirectorProfile.model_validate(data)
