"""Abort sequence for failed trials - two-tier infrastructure cleanup.

Implements the AbortSequence class that handles cleanup when a trial step fails.
Tier 1 attempts the full destroy-infrastructure service, and Tier 2 falls back
to a direct tofu destroy from the trial's benchmark-infrastructure directory.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.experiment.config import ExperimentConfig
from kasbench_controller.experiment.models import AbortResult
from kasbench_controller.logging import log_step
from kasbench_controller.services.destroy_infrastructure_service import (
    run_destroy_infrastructure,
)
from kasbench_controller.tofu import TofuRunner


class AbortSequence:
    """Handles trial cleanup on failure with a two-tier fallback strategy.

    Tier 1: Call the destroy-infrastructure service with all configured parameters.
    Tier 2: If Tier 1 fails, invoke TofuRunner.destroy() directly from the trial's
             benchmark-infrastructure directory with auto-approve enabled.

    If both tiers fail, the caller must halt all remaining trials to prevent
    infrastructure resource leakage.
    """

    def __init__(self, config: ExperimentConfig, logger: structlog.BoundLogger) -> None:
        """Initialize the AbortSequence.

        Args:
            config: The experiment configuration containing working directory,
                    run identifier, var files, variables, and ebs wait settings.
            logger: Structured logger instance for recording abort progress.
        """
        self._config = config
        self._logger = logger

    def execute(self, trial_identifier: str, autoscaler: str) -> AbortResult:
        """Execute the two-tier abort sequence.

        Args:
            trial_identifier: The identifier of the trial being aborted (e.g., "trial0001").
            autoscaler: The autoscaler assigned to this trial (for logging context).

        Returns:
            AbortResult indicating whether cleanup succeeded or
            if infrastructure could not be destroyed (halt required).
        """
        self._logger.info(
            "abort_sequence_start",
            trial_identifier=trial_identifier,
            autoscaler=autoscaler,
        )

        # --- Tier 1: Call destroy-infrastructure service ---
        try:
            log_step(
                self._logger,
                "abort_tier1_start",
                "info",
                trial_identifier=trial_identifier,
            )
            run_destroy_infrastructure(
                working_directory=self._config.working_directory,
                run_identifier=self._config.run_identifier,
                trial_identifier=trial_identifier,
                auto_approve=True,
                var_files=self._config.var_files,
                variables=self._config.variables,
                ebs_wait=self._config.ebs_wait,
                logger=self._logger,
            )
            log_step(
                self._logger,
                "abort_tier1_complete",
                "success",
                trial_identifier=trial_identifier,
            )
            return AbortResult(
                success=True,
                tier_reached=1,
                must_halt=False,
                error_message=None,
            )
        except KasbenchError as tier1_error:
            log_step(
                self._logger,
                "abort_tier1_failed",
                "failure",
                trial_identifier=trial_identifier,
                error=str(tier1_error),
            )

        # --- Tier 2: Direct tofu destroy from trial's benchmark-infrastructure directory ---
        tofu_directory = (
            self._config.working_directory
            / "benchmarks"
            / self._config.run_identifier
            / trial_identifier
            / "benchmark-infrastructure"
        )

        try:
            log_step(
                self._logger,
                "abort_tier2_start",
                "info",
                trial_identifier=trial_identifier,
                tofu_directory=str(tofu_directory),
            )
            tofu = TofuRunner(working_dir=tofu_directory, dry_run=False)
            tofu.destroy(
                var_files=self._config.var_files,
                variables=self._config.variables,
                run_id=trial_identifier,
                auto_approve=True,
            )
            log_step(
                self._logger,
                "abort_tier2_complete",
                "success",
                trial_identifier=trial_identifier,
            )
            return AbortResult(
                success=True,
                tier_reached=2,
                must_halt=False,
                error_message=None,
            )
        except KasbenchError as tier2_error:
            error_msg = (
                f"Abort sequence failed for trial '{trial_identifier}': "
                f"Both Tier 1 (destroy-infrastructure service) and "
                f"Tier 2 (direct tofu destroy from '{tofu_directory}') failed. "
                f"Tier 2 error: {tier2_error}. "
                f"Infrastructure could not be destroyed - halting experiment."
            )
            log_step(
                self._logger,
                "abort_tier2_failed",
                "failure",
                trial_identifier=trial_identifier,
                error=str(tier2_error),
                must_halt=True,
            )
            return AbortResult(
                success=False,
                tier_reached=2,
                must_halt=True,
                error_message=error_msg,
            )
