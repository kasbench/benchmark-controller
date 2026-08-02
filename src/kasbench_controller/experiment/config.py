"""Experiment configuration dataclass for run-experiment command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kasbench_controller.exceptions import ValidationError


@dataclass
class ExperimentConfig:
    """Holds all validated CLI parameters for a multi-trial experiment.

    Constructed from Click parameter parsing in the run-experiment command.
    """

    run_identifier: str
    trial_prefix: str
    autoscalers: list[str]  # includes duplicates
    trials_per_autoscaler: int
    run_duration: int
    working_directory: Path
    s3_bucket: str
    aws_region: str
    var_files: list[str]
    variables: list[str]
    auto_approve: bool
    runner_version: str
    health_timeout: int
    rollout_timeout: int
    cluster_cidr_range: str
    role_params: dict | None
    random_seed: int | None
    ebs_wait: int
    rerun_from_failed: bool
    halt_on_error: bool
    # Spot interruption handling
    spot_cooldown_seconds: int = 600
    spot_max_consecutive_interruptions: int = 3
    spot_poll_interval_seconds: int = 15

    @property
    def total_trials(self) -> int:
        """Compute the total number of trials for this experiment."""
        return len(self.autoscalers) * self.trials_per_autoscaler

    def __post_init__(self) -> None:
        """Validate configuration constraints after initialization."""
        if self.total_trials > 9999:
            raise ValidationError(
                f"Total trial count ({self.total_trials}) exceeds the "
                f"four-digit identifier limit of 9999. "
                f"Reduce autoscaler entries ({len(self.autoscalers)}) or "
                f"trials-per-autoscaler ({self.trials_per_autoscaler})."
            )
