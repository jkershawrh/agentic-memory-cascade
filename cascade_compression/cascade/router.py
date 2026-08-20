"""Cascade router — determines what happens after the cascade pipeline.

Signals that survive the cascade need inference. This router decides:
- Which tier (micro/macro) based on severity and finding type
- Which lane (classification/extraction/generation/reasoning) based on task
- Which model based on lane assignment and strategy
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from ..routing.corpora import (
    CORPORA_TO_ENDPOINT,
    resolve_lane,
    resolve_lane_model,
)

log = logging.getLogger(__name__)


@dataclass
class InferenceRequest:
    """A signal that survived the cascade and needs model inference."""
    request_id: UUID = field(default_factory=uuid4)
    signal_id: UUID = field(default_factory=uuid4)
    task_type: str = "classify"
    tier: str = "micro"
    lane: str = "classification"
    model: str = ""
    endpoint: str = ""
    prompt: str = ""
    max_tokens: int = 100
    context: Dict[str, Any] = field(default_factory=dict)


class CascadeRouter:
    """Routes post-cascade signals to the correct inference lane and model."""

    def __init__(
        self,
        industry: str = "basic",
        excluded_models: Optional[set] = None,
    ):
        self._industry = industry
        self._excluded_models = excluded_models or set()

    def route(
        self,
        signal_id: UUID,
        task_type: str,
        severity: str = "medium",
        prompt: str = "",
        context: Optional[Dict] = None,
    ) -> InferenceRequest:
        """Route a single post-cascade signal to inference."""
        tier = self._severity_to_tier(severity)
        lane = resolve_lane(task_type)
        model = resolve_lane_model(task_type, excluded_models=self._excluded_models)
        endpoint = CORPORA_TO_ENDPOINT.get(model, "") if model else ""

        return InferenceRequest(
            signal_id=signal_id,
            task_type=task_type,
            tier=tier,
            lane=lane,
            model=model or "",
            endpoint=endpoint,
            prompt=prompt,
            max_tokens=self._lane_max_tokens(lane),
            context=context or {},
        )

    def update_excluded(self, models: set) -> None:
        self._excluded_models = models

    @staticmethod
    def _severity_to_tier(severity: str) -> str:
        if severity in ("critical", "high"):
            return "macro"
        return "micro"

    @staticmethod
    def _lane_max_tokens(lane: str) -> int:
        return {
            "classification": 10,
            "extraction": 200,
            "generation": 300,
            "reasoning": 500,
            "embedding": 0,
        }.get(lane, 100)
