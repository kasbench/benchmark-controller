"""Progress management for experiment resumption and persistence to S3."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import boto3
import structlog
from botocore.exceptions import ClientError

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.experiment.config import ExperimentConfig
from kasbench_controller.experiment.models import ExperimentProgress, TrialAssignment

# Parameters excluded from comparison when validating resumption
_EXCLUDED_PARAMS = {"halt_on_error", "rerun_from_failed"}

# All trial steps in execution order
TRIAL_STEPS = [
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


class ProgressManager:
    """Manages the experiment progress file in S3.

    Maintains an in-memory progress state that is the source of truth during
    execution. Persists to S3 after each step for resumption on failure.
    """

    S3_UPLOAD_MAX_RETRIES = 3
    S3_UPLOAD_RETRY_DELAY = 5  # seconds

    def __init__(self, s3_bucket: str, aws_region: str, run_identifier: str) -> None:
        """Initialize the ProgressManager.

        Args:
            s3_bucket: S3 bucket name for progress file storage.
            aws_region: AWS region for S3 operations.
            run_identifier: Unique identifier for this experiment run.
        """
        self._s3_bucket = s3_bucket
        self._aws_region = aws_region
        self._run_identifier = run_identifier
        self._s3_key = f"{run_identifier}/experiment-progress.json"
        self._logger: structlog.BoundLogger = structlog.get_logger()
        self._progress: ExperimentProgress | None = None
        self._s3_client = boto3.client("s3", region_name=aws_region)

    @property
    def progress(self) -> ExperimentProgress | None:
        """Access the current in-memory progress state."""
        return self._progress

    def load_or_create(
        self, config: ExperimentConfig, schedule: list[TrialAssignment], effective_seed: int
    ) -> ExperimentProgress:
        """Load existing progress from S3 or create a new progress structure.

        Args:
            config: The current experiment configuration.
            schedule: The generated trial schedule.
            effective_seed: The seed used for schedule generation.

        Returns:
            The loaded or newly created ExperimentProgress.

        Raises:
            SystemExit: If the progress file exists but contains malformed JSON.
        """
        existing = self._download_progress()
        if existing is not None:
            self._progress = existing
            self._logger.info(
                "progress_loaded",
                run_identifier=self._run_identifier,
                trials_recorded=len(existing.trial_results),
            )
            return existing

        # Create new progress structure
        progress = ExperimentProgress(
            parameters=self._config_to_params(config),
            schedule=[
                {"trial_identifier": a.trial_identifier, "autoscaler": a.autoscaler}
                for a in schedule
            ],
            trial_results=[],
            effective_seed=effective_seed,
            version=1,
        )
        self._progress = progress
        self._upload_progress()
        self._logger.info(
            "progress_created",
            run_identifier=self._run_identifier,
            total_trials=len(schedule),
        )
        return progress

    def record_step_result(
        self, trial_id: str, step: str, status: str, error: str | None = None
    ) -> None:
        """Record a step completion/failure and persist to S3.

        Args:
            trial_id: The trial identifier (e.g., "trial0001").
            step: The step name (e.g., "init", "build-infrastructure").
            status: The step outcome ("success" or "failed").
            error: Optional error message for failed steps.
        """
        if self._progress is None:
            raise KasbenchError("ProgressManager has no loaded progress state.")

        # Find or create the trial result entry
        trial_entry = None
        for entry in self._progress.trial_results:
            if entry["trial_identifier"] == trial_id:
                trial_entry = entry
                break

        if trial_entry is None:
            # Find the autoscaler from the schedule
            autoscaler = ""
            for sched_entry in self._progress.schedule:
                if sched_entry["trial_identifier"] == trial_id:
                    autoscaler = sched_entry["autoscaler"]
                    break

            trial_entry = {
                "trial_identifier": trial_id,
                "autoscaler": autoscaler,
                "steps": [],
            }
            self._progress.trial_results.append(trial_entry)

        # Build step record
        step_record: dict = {
            "step": step,
            "status": status,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if error is not None:
            step_record["error"] = error

        trial_entry["steps"].append(step_record)

        # Persist to S3
        self._upload_progress()

    def record_trial_aborted(
        self, trial_id: str, reason: str, failed_step: str | None = None
    ) -> None:
        """Record a trial as aborted with the given reason.

        Preserves any step history already recorded for this trial.
        Adds an 'aborted' marker with reason and timestamp.

        Args:
            trial_id: The trial identifier (e.g., "trial0001").
            reason: The reason for aborting (e.g., "spot-interruption").
            failed_step: Optional name of the step that was interrupted.
        """
        if self._progress is None:
            raise KasbenchError("ProgressManager has no loaded progress state.")

        # Find or create the trial result entry
        trial_entry = None
        for entry in self._progress.trial_results:
            if entry["trial_identifier"] == trial_id:
                trial_entry = entry
                break

        if trial_entry is None:
            # Find the autoscaler from the schedule
            autoscaler = ""
            for sched_entry in self._progress.schedule:
                if sched_entry["trial_identifier"] == trial_id:
                    autoscaler = sched_entry["autoscaler"]
                    break

            trial_entry = {
                "trial_identifier": trial_id,
                "autoscaler": autoscaler,
                "steps": [],
            }
            self._progress.trial_results.append(trial_entry)

        # Mark as aborted — preserves existing steps list
        trial_entry["status"] = "aborted"
        trial_entry["reason"] = reason
        trial_entry["aborted_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        if failed_step is not None:
            trial_entry["failed_step"] = failed_step

        self._logger.info(
            "trial_aborted_recorded",
            trial_id=trial_id,
            reason=reason,
            failed_step=failed_step,
        )

        # Persist to S3
        self._upload_progress()

    def find_resume_point(self, rerun_from_failed: bool) -> tuple[int, str | None]:
        """Find the trial index and step to resume from.

        Skips trials marked as "aborted" and continues to the next pending
        trial in the schedule (including any appended replacement trials).

        Args:
            rerun_from_failed: If True, resume from the failed step within
                the first incomplete trial. If False, resume from the beginning
                of the first incomplete trial.

        Returns:
            A tuple of (trial_index, step_name) where trial_index is the 0-based
            index into the schedule, and step_name is None if the trial should
            start from the beginning, or the name of the step to resume from.
        """
        if self._progress is None:
            return (0, None)

        schedule = self._progress.schedule
        results = self._progress.trial_results

        for i, sched_entry in enumerate(schedule):
            trial_id = sched_entry["trial_identifier"]

            # Find the result entry for this trial
            trial_result = None
            for r in results:
                if r["trial_identifier"] == trial_id:
                    trial_result = r
                    break

            if trial_result is None:
                # No results recorded for this trial — start from beginning
                return (i, None)

            # Skip aborted trials — they have replacement trials appended
            if trial_result.get("status") == "aborted":
                continue

            steps = trial_result.get("steps", [])

            # Check if all steps completed successfully
            successful_steps = {s["step"] for s in steps if s["status"] == "success"}
            if len(successful_steps) == len(TRIAL_STEPS) and all(
                step in successful_steps for step in TRIAL_STEPS
            ):
                # This trial is complete, continue to next
                continue

            if rerun_from_failed:
                # Find the first failed step or the first step not yet recorded
                for step_name in TRIAL_STEPS:
                    step_records = [s for s in steps if s["step"] == step_name]
                    if not step_records:
                        # Step not yet recorded — resume from here
                        return (i, step_name)
                    last_record = step_records[-1]
                    if last_record["status"] == "failed":
                        # Found the failed step — resume from here
                        return (i, step_name)
                # All steps either success or unexpected state — start from beginning
                return (i, None)
            else:
                # Resume from the beginning of this incomplete trial
                return (i, None)

        # All trials complete (or all remaining are aborted with no pending replacements)
        return (len(schedule), None)

    def validate_parameters(self, config: ExperimentConfig) -> list[str]:
        """Compare stored parameters against current invocation.

        Returns a list of parameter names that differ between the stored
        progress and the current config. Parameters `halt_on_error` and
        `rerun_from_failed` are excluded from comparison.

        Args:
            config: The current experiment configuration.

        Returns:
            List of mismatched parameter names (empty if all match).
        """
        if self._progress is None:
            return []

        stored = self._progress.parameters
        current = self._config_to_params(config)

        mismatched: list[str] = []
        # Check all keys in current (which excludes halt_on_error, rerun_from_failed)
        all_keys = set(stored.keys()) | set(current.keys())
        for key in sorted(all_keys):
            stored_val = stored.get(key)
            current_val = current.get(key)
            if stored_val != current_val:
                mismatched.append(key)

        return mismatched

    def is_complete(self) -> bool:
        """Check if all non-aborted trials have completed successfully.

        Trials with status 'aborted' are excluded from completion checks.
        Only trials in the schedule that are not marked aborted must have
        all steps completed as 'success'.

        Returns:
            True if every non-aborted trial in the schedule has all steps
            recorded as "success", False otherwise.
        """
        if self._progress is None:
            return False

        schedule = self._progress.schedule
        results = self._progress.trial_results

        if not schedule:
            return True

        for sched_entry in schedule:
            trial_id = sched_entry["trial_identifier"]

            # Find result for this trial
            trial_result = None
            for r in results:
                if r["trial_identifier"] == trial_id:
                    trial_result = r
                    break

            if trial_result is None:
                return False

            # Skip aborted trials — they don't count toward completion
            if trial_result.get("status") == "aborted":
                continue

            steps = trial_result.get("steps", [])
            successful_steps = {s["step"] for s in steps if s["status"] == "success"}

            if not all(step in successful_steps for step in TRIAL_STEPS):
                return False

        return True

    def append_to_schedule(self, trial_identifier: str, autoscaler: str) -> None:
        """Append a replacement trial to the schedule and persist.

        Adds a new trial assignment to the end of the schedule list in the
        progress state and immediately persists the updated state to S3.

        Args:
            trial_identifier: The identifier for the new trial (e.g., "trial0004").
            autoscaler: The autoscaler to assign to the replacement trial.

        Raises:
            KasbenchError: If no progress state is loaded.
        """
        if self._progress is None:
            raise KasbenchError("ProgressManager has no loaded progress state.")

        self._progress.schedule.append(
            {"trial_identifier": trial_identifier, "autoscaler": autoscaler}
        )
        self._upload_progress()
        self._logger.info(
            "schedule_extended",
            trial_identifier=trial_identifier,
            autoscaler=autoscaler,
            total_scheduled=len(self._progress.schedule),
        )

    def persist(self) -> None:
        """Persist the current progress state to S3.

        Convenience method for callers that need to ensure progress is
        saved (e.g., before halting the experiment due to retry cap).
        """
        self._upload_progress()

    def _download_progress(self) -> ExperimentProgress | None:
        """Download and parse the progress file from S3.

        Returns:
            The parsed ExperimentProgress, or None if the file does not exist.

        Raises:
            SystemExit: If the file exists but cannot be parsed as valid JSON.
        """
        try:
            response = self._s3_client.get_object(
                Bucket=self._s3_bucket, Key=self._s3_key
            )
            body = response["Body"].read().decode("utf-8")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("NoSuchKey", "404"):
                return None
            raise

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._logger.error(
                "progress_file_corrupted",
                s3_bucket=self._s3_bucket,
                s3_key=self._s3_key,
                error=str(e),
            )
            raise SystemExit(
                f"Error: Progress file at s3://{self._s3_bucket}/{self._s3_key} "
                f"contains malformed JSON and cannot be read: {e}"
            )

        return ExperimentProgress(
            parameters=data.get("parameters", {}),
            schedule=data.get("schedule", []),
            trial_results=data.get("trial_results", []),
            effective_seed=data.get("effective_seed", 0),
            version=data.get("version", 1),
        )

    def _upload_progress(self) -> None:
        """Serialize and upload the progress state to S3 with retry.

        Retries up to 3 times with a 5-second delay between attempts.
        Logs a warning on permanent failure but does not raise.
        """
        if self._progress is None:
            return

        body = json.dumps(self._serialize_progress(), indent=2)

        for attempt in range(1, self.S3_UPLOAD_MAX_RETRIES + 1):
            try:
                self._s3_client.put_object(
                    Bucket=self._s3_bucket,
                    Key=self._s3_key,
                    Body=body.encode("utf-8"),
                    ContentType="application/json",
                )
                self._logger.debug(
                    "progress_uploaded",
                    s3_key=self._s3_key,
                    attempt=attempt,
                )
                return
            except (ClientError, Exception) as e:
                self._logger.warning(
                    "progress_upload_failed",
                    s3_key=self._s3_key,
                    attempt=attempt,
                    max_retries=self.S3_UPLOAD_MAX_RETRIES,
                    error=str(e),
                )
                if attempt < self.S3_UPLOAD_MAX_RETRIES:
                    time.sleep(self.S3_UPLOAD_RETRY_DELAY)

        self._logger.error(
            "progress_upload_exhausted",
            s3_key=self._s3_key,
            message=(
                f"Failed to upload progress file after "
                f"{self.S3_UPLOAD_MAX_RETRIES} attempts. "
                f"Continuing execution with in-memory state."
            ),
        )

    def _serialize_progress(self) -> dict:
        """Serialize the progress state to a dictionary for JSON encoding."""
        if self._progress is None:
            return {}

        return {
            "version": self._progress.version,
            "parameters": self._progress.parameters,
            "effective_seed": self._progress.effective_seed,
            "schedule": self._progress.schedule,
            "trial_results": self._progress.trial_results,
        }

    @staticmethod
    def _config_to_params(config: ExperimentConfig) -> dict:
        """Convert an ExperimentConfig to a parameters dict for storage.

        Excludes halt_on_error and rerun_from_failed as these are
        execution-time settings that should not affect resumption.
        """
        return {
            "run_identifier": config.run_identifier,
            "trial_prefix": config.trial_prefix,
            "autoscalers": config.autoscalers,
            "trials_per_autoscaler": config.trials_per_autoscaler,
            "run_duration": config.run_duration,
            "working_directory": str(config.working_directory),
            "s3_bucket": config.s3_bucket,
            "aws_region": config.aws_region,
            "var_files": config.var_files,
            "variables": config.variables,
            "auto_approve": config.auto_approve,
            "runner_version": config.runner_version,
            "health_timeout": config.health_timeout,
            "rollout_timeout": config.rollout_timeout,
            "cluster_cidr_range": config.cluster_cidr_range,
            "role_params": config.role_params,
            "ebs_wait": config.ebs_wait,
        }
