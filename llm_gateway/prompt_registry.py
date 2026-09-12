"""Central, versioned YAML prompt registry."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from llm_gateway.models import PromptDefinition


DEFAULT_PROMPTS_PATH = Path(__file__).resolve().parent / "prompts"
_SAFE_COMPONENT = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_VERSION = re.compile(r"^v[1-9][0-9]*$")


class PromptNotFoundError(LookupError):
    """Raised when a configured prompt or version does not exist."""


class PromptRegistry:
    """Load trusted prompts without allowing path traversal or agent-owned text."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = (root or DEFAULT_PROMPTS_PATH).resolve()
        self._cache: dict[tuple[str, str], PromptDefinition] = {}

    def load(self, group: str, version: str) -> PromptDefinition:
        if not _SAFE_COMPONENT.fullmatch(group):
            raise ValueError(f"Invalid prompt group: {group!r}")
        if not _SAFE_VERSION.fullmatch(version):
            raise ValueError(f"Invalid prompt version: {version!r}")

        cache_key = (group, version)
        if cache_key in self._cache:
            return self._cache[cache_key].model_copy(deep=True)

        path = self.root / group / f"{version}.yaml"
        if not path.is_file():
            raise PromptNotFoundError(
                f"Prompt '{group}' version '{version}' was not found"
            )
        with path.open("r", encoding="utf-8") as handle:
            definition = PromptDefinition.model_validate(yaml.safe_load(handle))
        if definition.version != version:
            raise ValueError(
                f"Prompt file version '{definition.version}' does not match '{version}'"
            )
        self._cache[cache_key] = definition
        return definition.model_copy(deep=True)

    def clear_cache(self) -> None:
        self._cache.clear()


__all__ = ["DEFAULT_PROMPTS_PATH", "PromptNotFoundError", "PromptRegistry"]

