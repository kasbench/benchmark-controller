"""Trial pipeline - sequential execution of all steps in a single trial.

Implements the TrialPipeline class that runs each trial through the 9-step
benchmark lifecycle: build-infrastructure, wait, initialize-runner,
benchmark-start, benchmark-monitor, benchmark-postprocessing, shutdown,
destroy-infrastructure, upload-logs.
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import TYPE_CHECKING, Callable

import structlog

from kasbench_controller.experiment.abort import AbortSequence
from kasbench_controller.experiment.config import ExperimentConfig
from kasbench_controller.experiment.experiment_logger import ExperimentLogger
from kasbench_controller.experiment.models import TrialAssignment, TrialResult
from kasbench_controller.experiment.progress import ProgressManager
from kasbench_controller.s3_uploader import S3Uploader
from kasbench_controller.services.benchmark_monitor_service import run_benchmark_monitor
from kasbench_controller.services.benchmark_postprocessing_service import (
    run_benchmark_postprocessing,
)
from kasbench_controller.services.benchmark_start_service import run_benchmark_start
from kasbench_controller.services.build_infrastructure_service import (
    run_build_infrastructure,
)
from kasbench_controller.services.destroy_infrastructure_service import (
    run_destroy_infrastructure,
)
from kasbench_controller.services.initialize_runner_service import run_initialize_runner
from kasbench_controller.services.shutdown_service import run_shutdown

if TYPE_CHECKING:
    from kasbench_controller.experiment.spot_detector import SpotInterruptionDetector


class TrialPipeline:
    """Executes a single trial through all steps sequentially.

    Each step calls the corresponding service function. On failure, the pipeline
    records the failed step, invokes the abort sequence (unless failure is during
    init), and returns a TrialResult indicating the failure.
    """

    STEPS = [
        "build-infrastructure",
        "wait",
        "initialize-runner",
        "benchmark-start",
        "benchmark-monitor",
        "benchmark-postprocessing",
        "shutdown",
        "destroy-infrastructure",
        "upload-logs",
    ]

    def __init__(
        self,
        config: ExperimentConfig,
        assignment: TrialAssignment,
        progress_manager: ProgressManager,
        abort_sequence: AbortSequence,
        logger: structlog.BoundLogger,
        experiment_logger: ExperimentLogger | None = None,
        detector_factory: Callable[[], SpotInterruptionDetector | None] | None = None,
    ) -> None:
        """Initialize the TrialPipeline.

        Args:
            config: The experiment configuration with all parameters.
            assignment: The trial assignment (trial_identifier, autoscaler).
            progress_manager: Manages progress persistence to S3.
            abort_sequence: Handles cleanup on step failure.
            logger: Structured logger instance bound with trial context.
            experiment_logger: Optional JSON Lines logger for experiment log file.
            detector_factory: Optional callable that creates a SpotInterruptionDetector.
                Called after build-infrastructure completes (or immediately for resumed
                trials that start from a later step). Returns None if detector cannot
                be created.
        """
        self._config = config
        self._assignment = assignment
        self._progress_manager = progress_manager
        self._abort_sequence = abort_sequence
        self._logger = logger
        self._experiment_logger = experiment_logger
        self._detector_factory = detector_factory
        self._detector: SpotInterruptionDetector | None = None
        self._interrupt_event: threading.Event | None = None

    def execute(self, start_from_step: str | None = None) -> TrialResult:
        """Execute the trial pipeline, optionally starting from a specific step.

        Args:
            start_from_step: If provided, skip all steps before this one.
                Must be a valid step name from STEPS.

        Returns:
            TrialResult indicating success or the step/error that caused abort.
        """
        steps_to_execute = self._resolve_steps(start_from_step)

        # For resumed trials starting after build-infrastructure, the infra files
        # already exist, so start the detector immediately.
        if start_from_step is not None and start_from_step != "build-infrastructure":
            self._start_detector()

        try:
            return self._execute_steps(steps_to_execute)
        finally:
            self._stop_detector()

    def _start_detector(self) -> None:
        """Create and start the spot interruption detector via the factory."""
        if self._detector_factory is None:
            return
        detector = self._detector_factory()
        if detector is not None:
            detector.start()
            self._detector = detector
            self._interrupt_event = detector.interrupt_event

    def _stop_detector(self) -> None:
        """Stop the spot interruption detector if running."""
        if self._detector is not None:
            self._detector.stop()
            self._detector = None
            self._interrupt_event = None

    def _execute_steps(self, steps_to_execute: list[str]) -> TrialResult:
        """Execute the given steps sequentially, checking for interrupts."""
        for step in steps_to_execute:
            # Check for spot interruption before starting step
            if self._interrupt_event and self._interrupt_event.is_set():
                self._logger.warning(
                    "spot_interruption_detected",
                    trial_identifier=self._assignment.trial_identifier,
                    autoscaler=self._assignment.autoscaler,
                    step=step,
                    phase="before_step",
                )
                return TrialResult(
                    trial_identifier=self._assignment.trial_identifier,
                    autoscaler=self._assignment.autoscaler,
                    success=False,
                    failed_step=step,
                    error_message="spot-interruption",
                    status="aborted",
                )

            self._logger.info(
                "step_start",
                trial_identifier=self._assignment.trial_identifier,
                autoscaler=self._assignment.autoscaler,
                step=step,
            )

            # Log step start to JSON Lines file
            if self._experiment_logger is not None:
                self._experiment_logger.log_step_start(
                    trial_identifier=self._assignment.trial_identifier,
                    autoscaler=self._assignment.autoscaler,
                    step=step,
                )

            try:
                self._execute_step(step)
            except Exception as exc:
                error_message = str(exc)
                tb = traceback.format_exc()

                # Extract stderr and return_code from structured exceptions
                stderr = getattr(exc, "stderr", None)
                return_code = getattr(exc, "return_code", None)

                log_kwargs: dict = {
                    "trial_identifier": self._assignment.trial_identifier,
                    "autoscaler": self._assignment.autoscaler,
                    "step": step,
                    "error": error_message,
                    "traceback": tb,
                }
                if stderr:
                    log_kwargs["stderr"] = stderr
                if return_code is not None:
                    log_kwargs["return_code"] = return_code

                self._logger.error("step_failed", **log_kwargs)

                # Log step failure to JSON Lines file
                if self._experiment_logger is not None:
                    self._experiment_logger.log_step_failed(
                        trial_identifier=self._assignment.trial_identifier,
                        autoscaler=self._assignment.autoscaler,
                        step=step,
                        error=error_message,
                        stderr=stderr,
                        return_code=return_code,
                        traceback=tb,
                    )

                # Record failed step in progress
                self._progress_manager.record_step_result(
                    trial_id=self._assignment.trial_identifier,
                    step=step,
                    status="failed",
                    error=error_message,
                )

                # Invoke abort sequence on step failure
                self._abort_sequence.execute(
                    trial_identifier=self._assignment.trial_identifier,
                    autoscaler=self._assignment.autoscaler,
                )

                return TrialResult(
                    trial_identifier=self._assignment.trial_identifier,
                    autoscaler=self._assignment.autoscaler,
                    success=False,
                    failed_step=step,
                    error_message=error_message,
                )

            # Record successful step in progress
            self._progress_manager.record_step_result(
                trial_id=self._assignment.trial_identifier,
                step=step,
                status="success",
            )

            # Start detector after build-infrastructure completes successfully
            if step == "build-infrastructure" and self._detector is None:
                self._start_detector()

            # Stop detector after benchmark-postprocessing completes.
            # From shutdown onward, nodes will become unreachable intentionally
            # and we don't want false-positive spot interruption signals.
            if step == "benchmark-postprocessing":
                self._stop_detector()

            # Check for spot interruption after step completes
            # (step history is already preserved above)
            if self._interrupt_event and self._interrupt_event.is_set():
                self._logger.warning(
                    "spot_interruption_detected",
                    trial_identifier=self._assignment.trial_identifier,
                    autoscaler=self._assignment.autoscaler,
                    step=step,
                    phase="after_step",
                )
                return TrialResult(
                    trial_identifier=self._assignment.trial_identifier,
                    autoscaler=self._assignment.autoscaler,
                    success=False,
                    failed_step=step,
                    error_message="spot-interruption",
                    status="aborted",
                )

            self._logger.info(
                "step_complete",
                trial_identifier=self._assignment.trial_identifier,
                autoscaler=self._assignment.autoscaler,
                step=step,
                outcome="success",
            )

            # Log step completion to JSON Lines file
            if self._experiment_logger is not None:
                self._experiment_logger.log_step_complete(
                    trial_identifier=self._assignment.trial_identifier,
                    autoscaler=self._assignment.autoscaler,
                    step=step,
                )

        return TrialResult(
            trial_identifier=self._assignment.trial_identifier,
            autoscaler=self._assignment.autoscaler,
            success=True,
            failed_step=None,
            error_message=None,
        )

    def _resolve_steps(self, start_from_step: str | None) -> list[str]:
        """Determine which steps to execute based on the start_from_step parameter."""
        if start_from_step is None:
            return list(self.STEPS)

        if start_from_step not in self.STEPS:
            raise ValueError(
                f"Invalid start_from_step '{start_from_step}'. "
                f"Must be one of: {self.STEPS}"
            )

        start_index = self.STEPS.index(start_from_step)
        return list(self.STEPS[start_index:])

    def _execute_step(self, step: str) -> None:
        """Dispatch execution to the appropriate service function for a step.

        Args:
            step: The step name to execute.

        Raises:
            KasbenchError: On any step failure.
            ValueError: If the step name is unrecognized.
        """
        if step == "build-infrastructure":
            self._step_build_infrastructure()
        elif step == "wait":
            self._step_wait()
        elif step == "initialize-runner":
            self._step_initialize_runner()
        elif step == "benchmark-start":
            self._step_benchmark_start()
        elif step == "benchmark-monitor":
            self._step_benchmark_monitor()
        elif step == "benchmark-postprocessing":
            self._step_benchmark_postprocessing()
        elif step == "shutdown":
            self._step_shutdown()
        elif step == "destroy-infrastructure":
            self._step_destroy_infrastructure()
        elif step == "upload-logs":
            self._step_upload_logs()
        else:
            raise ValueError(f"Unknown pipeline step: '{step}'")

    def _step_build_infrastructure(self) -> None:
        """Execute the build-infrastructure step."""
        run_build_infrastructure(
            working_directory=self._config.working_directory,
            run_identifier=self._config.run_identifier,
            trial_identifier=self._assignment.trial_identifier,
            autoscaler=self._assignment.autoscaler,
            aws_region=self._config.aws_region,
            s3_bucket=self._config.s3_bucket,
            run_duration=self._config.run_duration,
            auto_approve=True,
            var_files=self._config.var_files,
            variables=self._config.variables,
            logger=self._logger,
        )

    def _step_wait(self) -> None:
        """Execute the wait step - pause for infrastructure readiness.

        Sleeps for 120 seconds (2 minutes) to allow the infrastructure to become ready.
        """
        wait_seconds = 120
        self._logger.info(
            "wait_start",
            trial_identifier=self._assignment.trial_identifier,
            duration_seconds=wait_seconds,
            message=f"Waiting {wait_seconds}s for infrastructure to become ready...",
        )
        time.sleep(wait_seconds)

    def _step_initialize_runner(self) -> None:
        """Execute the initialize-runner step."""
        run_initialize_runner(
            working_directory=self._config.working_directory,
            run_identifier=self._config.run_identifier,
            trial_identifier=self._assignment.trial_identifier,
            runner_version=self._config.runner_version,
            health_timeout=self._config.health_timeout,
            rollout_timeout=self._config.rollout_timeout,
            cluster_cidr_range=self._config.cluster_cidr_range,
            logger=self._logger,
        )

    def _step_benchmark_start(self) -> None:
        """Execute the benchmark-start step."""
        run_benchmark_start(
            working_directory=self._config.working_directory,
            run_identifier=self._config.run_identifier,
            trial_identifier=self._assignment.trial_identifier,
            role_params=self._config.role_params,
            logger=self._logger,
        )

    def _step_benchmark_monitor(self) -> None:
        """Execute the benchmark-monitor step.

        Passes run_duration + 5 as the timeout. Both "success" and "failed"
        statuses are treated as valid outcomes (the trial continues either way).
        """
        timeout = self._config.run_duration + 5
        status = run_benchmark_monitor(
            working_directory=self._config.working_directory,
            run_identifier=self._config.run_identifier,
            trial_identifier=self._assignment.trial_identifier,
            timeout=timeout,
            logger=self._logger,
        )

        self._logger.info(
            "benchmark_monitor_result",
            trial_identifier=self._assignment.trial_identifier,
            benchmark_status=status,
            message=(
                "Benchmark completed with status '{}'. "
                "Proceeding to postprocessing.".format(status)
            ),
        )

    def _step_benchmark_postprocessing(self) -> None:
        """Execute the benchmark-postprocessing step."""
        run_benchmark_postprocessing(
            working_directory=self._config.working_directory,
            run_identifier=self._config.run_identifier,
            trial_identifier=self._assignment.trial_identifier,
            logger=self._logger,
        )

    def _step_shutdown(self) -> None:
        """Execute the shutdown step."""
        run_shutdown(
            working_directory=self._config.working_directory,
            run_identifier=self._config.run_identifier,
            trial_identifier=self._assignment.trial_identifier,
            logger=self._logger,
        )

    def _step_destroy_infrastructure(self) -> None:
        """Execute the destroy-infrastructure step."""
        run_destroy_infrastructure(
            working_directory=self._config.working_directory,
            run_identifier=self._config.run_identifier,
            trial_identifier=self._assignment.trial_identifier,
            auto_approve=True,
            var_files=self._config.var_files,
            variables=self._config.variables,
            ebs_wait=self._config.ebs_wait,
            logger=self._logger,
        )

    def _step_upload_logs(self) -> None:
        """Execute the upload-logs step.

        Uploads all files in the trial output directory to S3 at the path:
        {s3_bucket}/{run_identifier}/{trial_identifier}/benchmark-results
        """
        output_dir = (
            self._config.working_directory
            / self._config.run_identifier
            / self._assignment.trial_identifier
            / "output"
        )

        s3_prefix = (
            f"{self._config.run_identifier}"
            f"/{self._assignment.trial_identifier}"
            f"/benchmark-results"
        )

        uploader = S3Uploader(
            bucket=self._config.s3_bucket,
            region=self._config.aws_region,
            dry_run=False,
        )

        if not output_dir.exists():
            self._logger.warning(
                "upload_logs_skip",
                trial_identifier=self._assignment.trial_identifier,
                output_dir=str(output_dir),
                message="Trial output directory does not exist, skipping upload.",
            )
            return

        # Upload all files in the output directory recursively
        files_uploaded = 0
        for file_path in output_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(output_dir)
                s3_key = f"{s3_prefix}/{relative_path}"
                uploader.upload_file(file_path, s3_key)
                files_uploaded += 1

        self._logger.info(
            "upload_logs_complete",
            trial_identifier=self._assignment.trial_identifier,
            files_uploaded=files_uploaded,
            s3_prefix=f"s3://{self._config.s3_bucket}/{s3_prefix}",
        )
