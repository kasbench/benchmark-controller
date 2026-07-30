"""Experiment logger - writes structured JSON Lines log entries to a file.

Provides an ExperimentLogger class that writes structured log events to
a JSON Lines file at {working_directory}/benchmarks/{run_identifier}/experiment.log.
Each entry includes an ISO 8601 UTC timestamp, trial context, and outcome.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


class ExperimentLogger:
    """Writes structured JSON Lines log entries to the experiment log file.

    The log file is located at:
        {working_directory}/benchmarks/{run_identifier}/experiment.log

    Each line is a self-contained JSON object with at minimum:
        - timestamp (ISO 8601 UTC)
        - level (info or error)
        - event (step, trial, experiment)

    On file creation or write failure, the logger exits with a non-zero
    exit code and an error message to stderr per Requirement 7.5.
    """

    def __init__(self, working_directory: Path, run_identifier: str) -> None:
        """Initialize the ExperimentLogger.

        Creates (or opens for append) the experiment.log file. Exits with
        error if the file cannot be created or written to.

        Args:
            working_directory: The base working directory for the experiment.
            run_identifier: The unique identifier for this experiment run.
        """
        self._log_path = (
            working_directory / "benchmarks" / run_identifier / "experiment.log"
        )
        self._file = None
        self._open_log_file()

    def _open_log_file(self) -> None:
        """Open the log file for appending, creating parent directories as needed.

        Exits with non-zero status if the file cannot be created or opened.
        """
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._log_path, "a", encoding="utf-8")  # noqa: SIM115
        except OSError as e:
            print(
                f"Error: Cannot create or write to log file "
                f"'{self._log_path}': {e}",
                file=sys.stderr,
            )
            sys.exit(1)

    def _write_entry(self, entry: dict) -> None:
        """Serialize and write a single log entry as a JSON line.

        Exits with non-zero status if writing fails.

        Args:
            entry: The log entry dictionary to serialize.
        """
        try:
            line = json.dumps(entry, default=str)
            self._file.write(line + "\n")
            self._file.flush()
        except OSError as e:
            print(
                f"Error: Cannot write to log file '{self._log_path}': {e}",
                file=sys.stderr,
            )
            sys.exit(1)

    @staticmethod
    def _utc_timestamp() -> str:
        """Return the current UTC time as ISO 8601 string."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def log_step_start(
        self,
        trial_identifier: str,
        autoscaler: str,
        step: str,
    ) -> None:
        """Log the start of a pipeline step.

        Args:
            trial_identifier: The trial ID (e.g., "trial0001").
            autoscaler: The assigned autoscaler (e.g., "keda").
            step: The step name (e.g., "build-infrastructure").
        """
        entry = {
            "timestamp": self._utc_timestamp(),
            "level": "info",
            "event": "step",
            "trial_identifier": trial_identifier,
            "autoscaler": autoscaler,
            "step": step,
            "outcome": "started",
        }
        self._write_entry(entry)

    def log_step_complete(
        self,
        trial_identifier: str,
        autoscaler: str,
        step: str,
    ) -> None:
        """Log the successful completion of a pipeline step.

        Args:
            trial_identifier: The trial ID (e.g., "trial0001").
            autoscaler: The assigned autoscaler (e.g., "keda").
            step: The step name (e.g., "build-infrastructure").
        """
        entry = {
            "timestamp": self._utc_timestamp(),
            "level": "info",
            "event": "step",
            "trial_identifier": trial_identifier,
            "autoscaler": autoscaler,
            "step": step,
            "outcome": "success",
        }
        self._write_entry(entry)

    def log_step_failed(
        self,
        trial_identifier: str,
        autoscaler: str,
        step: str,
        error: str,
        stderr: str | None = None,
        return_code: int | None = None,
        traceback: str | None = None,
    ) -> None:
        """Log a failed pipeline step with error details.

        Args:
            trial_identifier: The trial ID (e.g., "trial0001").
            autoscaler: The assigned autoscaler (e.g., "keda").
            step: The step name (e.g., "initialize-runner").
            error: The error message.
            stderr: Captured stderr output, if available.
            return_code: The command return code, if available.
            traceback: Python stack trace, if available.
        """
        entry: dict = {
            "timestamp": self._utc_timestamp(),
            "level": "error",
            "event": "step",
            "trial_identifier": trial_identifier,
            "autoscaler": autoscaler,
            "step": step,
            "outcome": "failure",
            "error": error,
        }
        if stderr is not None:
            entry["stderr"] = stderr
        if return_code is not None:
            entry["return_code"] = return_code
        if traceback is not None:
            entry["traceback"] = traceback
        self._write_entry(entry)

    def log_trial_start(
        self,
        trial_identifier: str,
        autoscaler: str,
        sequence_number: int,
        total_trials: int,
    ) -> None:
        """Log the start of a trial.

        Args:
            trial_identifier: The trial ID (e.g., "trial0001").
            autoscaler: The assigned autoscaler (e.g., "keda").
            sequence_number: The 1-based position in the schedule.
            total_trials: Total number of trials in the experiment.
        """
        entry = {
            "timestamp": self._utc_timestamp(),
            "level": "info",
            "event": "trial",
            "trial_identifier": trial_identifier,
            "autoscaler": autoscaler,
            "step": "trial-start",
            "outcome": "started",
            "sequence_number": sequence_number,
            "total_trials": total_trials,
        }
        self._write_entry(entry)

    def log_trial_complete(
        self,
        trial_identifier: str,
        autoscaler: str,
    ) -> None:
        """Log the successful completion of a trial.

        Args:
            trial_identifier: The trial ID (e.g., "trial0001").
            autoscaler: The assigned autoscaler (e.g., "keda").
        """
        entry = {
            "timestamp": self._utc_timestamp(),
            "level": "info",
            "event": "trial",
            "trial_identifier": trial_identifier,
            "autoscaler": autoscaler,
            "step": "trial-complete",
            "outcome": "success",
        }
        self._write_entry(entry)

    def log_trial_aborted(
        self,
        trial_identifier: str,
        autoscaler: str,
        failed_step: str,
        error: str,
    ) -> None:
        """Log that a trial was aborted due to a step failure.

        Args:
            trial_identifier: The trial ID (e.g., "trial0001").
            autoscaler: The assigned autoscaler (e.g., "keda").
            failed_step: The step that caused the abort.
            error: The error message.
        """
        entry = {
            "timestamp": self._utc_timestamp(),
            "level": "error",
            "event": "trial",
            "trial_identifier": trial_identifier,
            "autoscaler": autoscaler,
            "step": failed_step,
            "outcome": "aborted",
            "error": error,
        }
        self._write_entry(entry)

    def log_experiment_start(
        self,
        run_identifier: str,
        total_trials: int,
        effective_seed: int,
    ) -> None:
        """Log the start of the experiment.

        Args:
            run_identifier: The experiment run identifier.
            total_trials: Total number of trials to execute.
            effective_seed: The random seed used for scheduling.
        """
        entry = {
            "timestamp": self._utc_timestamp(),
            "level": "info",
            "event": "experiment",
            "trial_identifier": "",
            "autoscaler": "",
            "step": "experiment-start",
            "outcome": "started",
            "run_identifier": run_identifier,
            "total_trials": total_trials,
            "effective_seed": effective_seed,
        }
        self._write_entry(entry)

    def log_experiment_complete(self, run_identifier: str) -> None:
        """Log the successful completion of the full experiment.

        Args:
            run_identifier: The experiment run identifier.
        """
        entry = {
            "timestamp": self._utc_timestamp(),
            "level": "info",
            "event": "experiment",
            "trial_identifier": "",
            "autoscaler": "",
            "step": "experiment-complete",
            "outcome": "success",
            "run_identifier": run_identifier,
        }
        self._write_entry(entry)

    def close(self) -> None:
        """Close the log file handle."""
        if self._file is not None and not self._file.closed:
            self._file.close()

    @property
    def log_path(self) -> Path:
        """Return the path to the log file."""
        return self._log_path
