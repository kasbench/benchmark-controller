"""Experiment orchestrator - top-level controller for multi-trial experiments.

Ties together TrialScheduler, ProgressManager, AbortSequence, TrialPipeline,
and ExperimentLogger to execute a full experiment run with support for
resumption, parameter validation, and configurable error handling.
"""

from __future__ import annotations

import sys

import structlog

from kasbench_controller.experiment.abort import AbortSequence
from kasbench_controller.experiment.config import ExperimentConfig
from kasbench_controller.experiment.experiment_logger import ExperimentLogger
from kasbench_controller.experiment.models import AbortResult
from kasbench_controller.experiment.pipeline import TrialPipeline
from kasbench_controller.experiment.progress import ProgressManager
from kasbench_controller.experiment.scheduler import TrialScheduler
from kasbench_controller.services.init_service import run_init


class _TrackingAbortSequence(AbortSequence):
    """AbortSequence wrapper that records the last AbortResult for the orchestrator."""

    def __init__(self, config: ExperimentConfig, logger: structlog.BoundLogger) -> None:
        super().__init__(config=config, logger=logger)
        self.last_result: AbortResult | None = None

    def execute(self, trial_identifier: str, autoscaler: str) -> AbortResult:
        """Execute abort and store the result."""
        result = super().execute(trial_identifier=trial_identifier, autoscaler=autoscaler)
        self.last_result = result
        return result


class ExperimentOrchestrator:
    """Top-level class that orchestrates a multi-trial experiment.

    Coordinates schedule generation, progress management, and sequential
    trial execution through the full benchmark lifecycle. Supports resumption
    from prior progress and respects halt-on-error configuration.
    """

    def __init__(self, config: ExperimentConfig, logger: structlog.BoundLogger) -> None:
        """Initialize the ExperimentOrchestrator.

        Args:
            config: The fully validated experiment configuration.
            logger: Structured logger instance for recording orchestrator events.
        """
        self._config = config
        self._logger = logger

    def run(self) -> int:
        """Execute the full experiment. Returns exit code (0 = success).

        The orchestrator:
        1. Creates the experiment logger (JSON Lines file).
        2. Generates the trial schedule via TrialScheduler.
        3. Loads or creates progress state via ProgressManager.
        4. Validates parameters against stored progress (if resuming and not rerun_from_failed).
        5. Checks if the experiment is already complete.
        6. Finds the resume point (trial index and optional step name).
        7. Iterates through remaining trials, creating a TrialPipeline for each.
        8. Respects halt_on_error flag — halts all remaining trials on failure.

        Returns:
            0 if all trials completed (or experiment was already complete),
            1 if execution was halted due to error.
        """
        # Step 0: Create experiment logger (exits on failure per Req 7.5)
        experiment_logger = ExperimentLogger(
            working_directory=self._config.working_directory,
            run_identifier=self._config.run_identifier,
        )

        # Step 1: Generate schedule
        scheduler = TrialScheduler(config=self._config, logger=self._logger)
        schedule = scheduler.generate_schedule()

        self._logger.info(
            "schedule_generated",
            total_trials=len(schedule),
            effective_seed=scheduler.effective_seed,
            autoscalers=self._config.autoscalers,
            trials_per_autoscaler=self._config.trials_per_autoscaler,
        )

        # Log experiment start to the JSON Lines file
        experiment_logger.log_experiment_start(
            run_identifier=self._config.run_identifier,
            total_trials=len(schedule),
            effective_seed=scheduler.effective_seed,
        )

        # Step 2: Load or create progress
        progress_manager = ProgressManager(
            s3_bucket=self._config.s3_bucket,
            aws_region=self._config.aws_region,
            run_identifier=self._config.run_identifier,
        )
        progress = progress_manager.load_or_create(
            config=self._config,
            schedule=schedule,
            effective_seed=scheduler.effective_seed,
        )

        # Step 3: Validate parameters if progress exists and not rerun_from_failed
        if progress.trial_results and not self._config.rerun_from_failed:
            mismatches = progress_manager.validate_parameters(self._config)
            if mismatches:
                mismatch_details = ", ".join(mismatches)
                self._logger.error(
                    "parameter_mismatch",
                    mismatched_parameters=mismatches,
                    message=(
                        f"Cannot resume experiment: parameters differ from "
                        f"stored progress. Mismatched: {mismatch_details}"
                    ),
                )
                sys.exit(
                    f"Error: Cannot resume experiment '{self._config.run_identifier}'. "
                    f"The following parameters differ from the stored progress file: "
                    f"{mismatch_details}. "
                    f"Use the same parameters as the original run, or delete the "
                    f"progress file to start fresh."
                )

        # Step 4: Check if experiment is already complete
        if progress_manager.is_complete():
            self._logger.info(
                "experiment_already_complete",
                run_identifier=self._config.run_identifier,
                message="All trials have already completed successfully.",
            )
            return 0

        # Step 5: Find resume point
        trial_index, step_name = progress_manager.find_resume_point(
            rerun_from_failed=self._config.rerun_from_failed
        )

        if trial_index > 0 or step_name is not None:
            self._logger.info(
                "resuming_experiment",
                run_identifier=self._config.run_identifier,
                resume_trial_index=trial_index,
                resume_step=step_name,
                message=(
                    f"Resuming from trial index {trial_index}"
                    f"{f', step {step_name}' if step_name else ''}."
                ),
            )

        # Run init once for the entire experiment (only on fresh start, not resume)
        if trial_index == 0 and step_name is None:
            self._logger.info("experiment_init_start", run_identifier=self._config.run_identifier)
            run_init(
                working_directory=self._config.working_directory,
                run_identifier=self._config.run_identifier,
                logger=self._logger,
                force=False,
            )
            self._logger.info("experiment_init_complete", run_identifier=self._config.run_identifier)

        # Step 6: Iterate through trials from the resume point
        for i in range(trial_index, len(schedule)):
            assignment = schedule[i]

            trial_logger = self._logger.bind(
                trial_identifier=assignment.trial_identifier,
                autoscaler=assignment.autoscaler,
                sequence_number=assignment.sequence_number,
            )

            trial_logger.info(
                "trial_start",
                trial_index=i,
                total_trials=len(schedule),
            )

            # Log trial start to the JSON Lines file
            experiment_logger.log_trial_start(
                trial_identifier=assignment.trial_identifier,
                autoscaler=assignment.autoscaler,
                sequence_number=assignment.sequence_number,
                total_trials=len(schedule),
            )

            # Create a tracking AbortSequence so we can inspect must_halt after pipeline runs
            abort_sequence = _TrackingAbortSequence(
                config=self._config,
                logger=trial_logger,
            )

            # Create TrialPipeline for this trial
            pipeline = TrialPipeline(
                config=self._config,
                assignment=assignment,
                progress_manager=progress_manager,
                abort_sequence=abort_sequence,
                logger=trial_logger,
                experiment_logger=experiment_logger,
            )

            # Determine start_from_step: only for the first trial in resumption
            current_step = step_name if i == trial_index else None

            # Execute the trial pipeline
            result = pipeline.execute(start_from_step=current_step)

            if not result.success:
                trial_logger.error(
                    "trial_failed",
                    failed_step=result.failed_step,
                    error_message=result.error_message,
                )

                # Log trial abort to the JSON Lines file
                experiment_logger.log_trial_aborted(
                    trial_identifier=result.trial_identifier,
                    autoscaler=result.autoscaler,
                    failed_step=result.failed_step or "unknown",
                    error=result.error_message or "Unknown error",
                )

                # Check if abort returned must_halt (both tiers failed → infrastructure leak risk)
                if abort_sequence.last_result is not None and abort_sequence.last_result.must_halt:
                    self._logger.error(
                        "abort_must_halt",
                        run_identifier=self._config.run_identifier,
                        failed_trial=result.trial_identifier,
                        message=(
                            "Abort sequence failed for trial — infrastructure could "
                            "not be destroyed. Halting all remaining trials."
                        ),
                    )
                    return 1

                # Check halt_on_error flag
                if self._config.halt_on_error:
                    self._logger.info(
                        "halt_on_error_triggered",
                        run_identifier=self._config.run_identifier,
                        failed_trial=result.trial_identifier,
                        failed_step=result.failed_step,
                        message=(
                            "halt-on-error is enabled. Stopping experiment "
                            "after trial failure."
                        ),
                    )
                    return 1

                # Otherwise continue to next trial
                trial_logger.info(
                    "continuing_after_failure",
                    failed_trial=result.trial_identifier,
                    message="Continuing to next trial after failure.",
                )
            else:
                trial_logger.info(
                    "trial_complete",
                    trial_index=i,
                    total_trials=len(schedule),
                )

                # Log trial completion to the JSON Lines file
                experiment_logger.log_trial_complete(
                    trial_identifier=assignment.trial_identifier,
                    autoscaler=assignment.autoscaler,
                )

        # Step 7: All trials done
        self._logger.info(
            "experiment_complete",
            run_identifier=self._config.run_identifier,
            total_trials=len(schedule),
            message="All trials have completed.",
        )

        # Log experiment completion to the JSON Lines file
        experiment_logger.log_experiment_complete(
            run_identifier=self._config.run_identifier,
        )
        experiment_logger.close()
        return 0
