"""Sanitized application logging and in-process demo accounting."""

from __future__ import annotations

import json
import logging
from threading import RLock

from llm_gateway.models import LLMCallRecord, LLMCallStatus


class GatewayTelemetry:
    """Record model-call metadata without raw prompts or responses."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("neutail.llm_gateway")
        self._records: list[LLMCallRecord] = []
        self._lock = RLock()

    def record(self, call: LLMCallRecord) -> None:
        if not isinstance(call, LLMCallRecord):
            call = LLMCallRecord.model_validate(call)
        with self._lock:
            self._records.append(call.model_copy(deep=True))

        log_method = (
            self._logger.info
            if call.status in {LLMCallStatus.SUCCESS, LLMCallStatus.FALLBACK_SUCCESS}
            else self._logger.warning
        )
        log_method(
            "llm_call %s",
            json.dumps(call.model_dump(mode="json"), sort_keys=True),
        )

    @property
    def records(self) -> list[LLMCallRecord]:
        with self._lock:
            return [record.model_copy(deep=True) for record in self._records]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


__all__ = ["GatewayTelemetry"]

