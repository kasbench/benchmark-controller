"""Data models for the experiment orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrialAssignment:
    """A single trial-to-autoscaler assignment in the experiment schedule."""

    trial_identifier: str  # e.g., "trial0001"
    autoscaler: str  # e.g., "hpa"
    sequence_number: int  # 1-based position in schedule


@dataclass
class TrialResult:
    """Outcome of executing a single trial through the pipeline."""

    trial_identifier: str
    autoscaler: str
    success: bool
    failed_step: str | None = None
    error_message: str | None = None


@dataclass
class AbortResult:
    """Outcome of the abort sequence for a failed trial."""

    success: bool
    tier_reached: int  # 1 = destroy-infrastructure worked, 2 = direct tofu worked
    must_halt: bool  # True if both tiers failed
    error_message: str | None = None


@dataclass
class ExperimentProgress:
    """Persisted experiment state for resumption from S3."""

    parameters: dict  # Stored invocation parameters
    schedule: list[dict]  # [{trial_identifier, autoscaler}, ...]
    trial_results: list[dict]  # [{trial_identifier, steps: [{step, status, timestamp, error?}]}]
    effective_seed: int
    version: int = 1
