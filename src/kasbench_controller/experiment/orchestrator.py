"""Experiment orchestrator - top-level controller for multi-trial experiments.

Ties together TrialScheduler, ProgressManager, AbortSequence, TrialPipeline,
and ExperimentLogger to execute a full experiment run with support for
resumption, parameter validation, and configurable error handling.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import structlog

from kasbench_controller.experiment.abort import AbortSequence
from kasbench_controller.experiment.config import ExperimentConfig
from kasbench_controller.experiment.experiment_logger import ExperimentLogger
from kasbench_controller.experiment.models import AbortResult, TrialAssignment
from kasbench_controller.experiment.pipeline import TrialPipeline
from kasbench_controller.experiment.progress import ProgressManager
from kasbench_controller.experiment.scheduler import TrialScheduler
from kasbench_controller.experiment.spot_detector import SpotInterruptionDetector
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
        # Use a while loop so dynamically appended replacement trials are iterated over
        consecutive_interruptions = 0
        i = trial_index
        while i < len(schedule):
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

            # Start spot interruption detector for this trial
            detector = self._create_detector(assignment, trial_logger)
            if detector:
                detector.start()

            try:
                # Create TrialPipeline for this trial
                pipeline = TrialPipeline(
                    config=self._config,
                    assignment=assignment,
                    progress_manager=progress_manager,
                    abort_sequence=abort_sequence,
                    logger=trial_logger,
                    experiment_logger=experiment_logger,
                    interrupt_event=detector.interrupt_event if detector else None,
                )

                # Determine start_from_step: only for the first trial in resumption
                current_step = step_name if i == trial_index else None

                # Execute the trial pipeline
                result = pipeline.execute(start_from_step=current_step)
            finally:
                # Stop detector after trial completes (success or failure)
                if detector:
                    detector.stop()

            if not result.success and result.error_message == "spot-interruption":
                # --- Spot interruption handling ---
                consecutive_interruptions += 1

                trial_logger.warning(
                    "spot_interruption_detected",
                    trial_identifier=result.trial_identifier,
                    failed_step=result.failed_step,
                    consecutive_interruptions=consecutive_interruptions,
                )

                # Record as aborted in progress
                progress_manager.record_trial_aborted(
                    trial_id=result.trial_identifier,
                    reason="spot-interruption",
                    failed_step=result.failed_step,
                )

                # Log trial abort to the JSON Lines file
                experiment_logger.log_trial_aborted(
                    trial_identifier=result.trial_identifier,
                    autoscaler=result.autoscaler,
                    failed_step=result.failed_step or "unknown",
                    error="spot-interruption",
                )

                # Run abort sequence for infrastructure cleanup
                abort_result = abort_sequence.execute(
                    trial_identifier=result.trial_identifier,
                    autoscaler=result.autoscaler,
                )
                if abort_result.must_halt:
                    self._logger.error(
                        "abort_must_halt",
                        run_identifier=self._config.run_identifier,
                        failed_trial=result.trial_identifier,
                        message=(
                            "Abort sequence failed for spot-interrupted trial — "
                            "infrastructure could not be destroyed. Halting all "
                            "remaining trials."
                        ),
                    )
                    return 1

                # Check retry cap
                max_consecutive = self._config.spot_max_consecutive_interruptions
                if max_consecutive > 0 and consecutive_interruptions >= max_consecutive:
                    self._logger.error(
                        "spot_retry_cap_reached",
                        run_identifier=self._config.run_identifier,
                        consecutive_interruptions=consecutive_interruptions,
                        max_consecutive=max_consecutive,
                        message=(
                            f"Spot interruption retry cap reached "
                            f"({consecutive_interruptions}/{max_consecutive}). "
                            f"Halting experiment."
                        ),
                    )
                    progress_manager.persist()
                    return 1

                # Append replacement trial to schedule
                new_trial_id = self._config.trial_prefix + str(
                    len(schedule) + 1
                ).zfill(4)
                progress_manager.append_to_schedule(new_trial_id, assignment.autoscaler)
                new_assignment = TrialAssignment(
                    trial_identifier=new_trial_id,
                    autoscaler=assignment.autoscaler,
                    sequence_number=len(schedule) + 1,
                )
                schedule.append(new_assignment)

                trial_logger.info(
                    "replacement_trial_appended",
                    new_trial_id=new_trial_id,
                    autoscaler=assignment.autoscaler,
                    schedule_length=len(schedule),
                )

                # Cooldown before next trial
                self._cooldown(self._config.spot_cooldown_seconds)

            elif not result.success:
                # --- Normal (non-spot) failure handling ---
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
                # --- Trial completed successfully ---
                consecutive_interruptions = 0

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

            i += 1

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

    def _cooldown(self, seconds: int) -> None:
        """Wait for the specified cooldown period, logging progress periodically.

        Logs the remaining cooldown time every 60 seconds so the operator can
        monitor progress during the wait.

        Args:
            seconds: Total number of seconds to wait.
        """
        if seconds <= 0:
            return

        self._logger.info(
            "spot_cooldown_start",
            cooldown_seconds=seconds,
            message=f"Starting {seconds}s cooldown after spot interruption.",
        )

        elapsed = 0
        while elapsed < seconds:
            remaining = seconds - elapsed
            sleep_chunk = min(60, remaining)
            time.sleep(sleep_chunk)
            elapsed += sleep_chunk
            remaining = seconds - elapsed
            if remaining > 0:
                self._logger.info(
                    "spot_cooldown_remaining",
                    remaining_seconds=remaining,
                    message=f"{remaining}s remaining in cooldown.",
                )

        self._logger.info(
            "spot_cooldown_complete",
            cooldown_seconds=seconds,
            message="Cooldown period complete. Resuming trials.",
        )

    def _create_detector(
        self, assignment: TrialAssignment, logger: structlog.BoundLogger
    ) -> SpotInterruptionDetector | None:
        """Create a SpotInterruptionDetector for the given trial assignment.

        Attempts to resolve cluster node IPs from the trial's trial_config.json.
        If the config doesn't exist yet (fresh trial that hasn't built infrastructure),
        returns None. The detector will gracefully handle SSH failures if nodes aren't
        reachable yet.

        Args:
            assignment: The trial assignment containing trial_identifier and autoscaler.
            logger: Bound logger for this trial.

        Returns:
            A SpotInterruptionDetector instance, or None if node IPs cannot be resolved.
        """
        node_ips = self._get_node_ips(assignment)
        if not node_ips:
            logger.debug(
                "spot_detector_skipped",
                trial_identifier=assignment.trial_identifier,
                reason="No node IPs available (trial_config.json not found or empty).",
            )
            return None

        ssh_key_path = self._get_ssh_key_path(assignment)
        if not ssh_key_path:
            logger.debug(
                "spot_detector_skipped",
                trial_identifier=assignment.trial_identifier,
                reason="SSH key file not found.",
            )
            return None

        return SpotInterruptionDetector(
            node_ips=node_ips,
            ssh_key_path=str(ssh_key_path),
            ssh_user="ubuntu",
            poll_interval_seconds=self._config.spot_poll_interval_seconds,
            logger=logger,
        )

    def _get_node_ips(self, assignment: TrialAssignment) -> list[str]:
        """Resolve cluster node IPs from the trial's trial_config.json.

        Reads the trial config written by the build-infrastructure step to extract
        control plane and worker node private IPs.

        Args:
            assignment: The trial assignment containing trial_identifier.

        Returns:
            List of node IPs (control plane + workers), or empty list if unavailable.
        """
        trial_config_path = (
            self._config.working_directory
            / self._config.run_identifier
            / assignment.trial_identifier
            / "output"
            / "trial_config.json"
        )

        if not trial_config_path.exists():
            return []

        try:
            data = json.loads(trial_config_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []

        node_ips: list[str] = []

        # Add control plane IP
        control_plane_ip = data.get("control_plane_private_ip")
        if control_plane_ip:
            node_ips.append(control_plane_ip)

        # Add worker node IPs (amd64 and arm64)
        amd_ips = data.get("amd_worker_private_ips", [])
        arm_ips = data.get("arm_worker_private_ips", [])
        node_ips.extend(amd_ips)
        node_ips.extend(arm_ips)

        return node_ips

    def _get_ssh_key_path(self, assignment: TrialAssignment) -> Path | None:
        """Resolve the SSH private key path for a trial.

        The SSH key is generated during build-infrastructure and stored at:
        {working_dir}/{run_id}/{trial_id}/benchmark-infrastructure/artifacts/{trial_id}/fleet_key.pem

        Args:
            assignment: The trial assignment containing trial_identifier.

        Returns:
            Path to the SSH key file, or None if it doesn't exist.
        """
        key_path = (
            self._config.working_directory
            / self._config.run_identifier
            / assignment.trial_identifier
            / "benchmark-infrastructure"
            / "artifacts"
            / assignment.trial_identifier
            / "fleet_key.pem"
        )

        if key_path.exists():
            return key_path
        return None
