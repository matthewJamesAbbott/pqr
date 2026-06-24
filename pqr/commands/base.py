from __future__ import annotations

from abc import ABC, abstractmethod

from pqr.pipeline.state import PipelineState, StepResult


class Command(ABC):
    """Command Pattern: turns strings into state-change objects."""

    @abstractmethod
    def execute(self, state: PipelineState) -> StepResult: ...
