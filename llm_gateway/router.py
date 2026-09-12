"""Configuration-driven logical model routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from llm_gateway.models import ModelRoute


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "models.yaml"


class UnknownUseCaseError(LookupError):
    """Raised when no governed model route exists for a use case."""


class ModelRouter:
    """Resolve business use cases to provider-neutral logical routes."""

    def __init__(self, routes: dict[str, ModelRoute]) -> None:
        if not routes:
            raise ValueError("At least one model route is required")
        self._routes = dict(routes)

    @classmethod
    def from_yaml(cls, path: Optional[Path] = None) -> "ModelRouter":
        config_path = path or DEFAULT_CONFIG_PATH
        with config_path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle) or {}
        raw_routes = document.get("routes")
        if not isinstance(raw_routes, dict):
            raise ValueError("Model config must contain a 'routes' mapping")
        routes = {
            use_case: ModelRoute.model_validate(
                {"use_case": use_case, **_require_mapping(config, use_case)}
            )
            for use_case, config in raw_routes.items()
        }
        return cls(routes)

    def resolve(self, use_case: str) -> ModelRoute:
        normalized = use_case.strip()
        try:
            return self._routes[normalized].model_copy(deep=True)
        except KeyError as exc:
            raise UnknownUseCaseError(
                f"No LLM route is configured for use case '{normalized}'"
            ) from exc

    def list_use_cases(self) -> list[str]:
        return sorted(self._routes)


def _require_mapping(value: Any, use_case: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Route '{use_case}' must be a mapping")
    return value


def provider_from_model(model: str) -> str:
    """Extract the LiteLLM provider prefix for telemetry."""

    return model.split("/", 1)[0] if "/" in model else "unknown"


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ModelRouter",
    "UnknownUseCaseError",
    "provider_from_model",
]

