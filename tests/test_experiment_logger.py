"""Tests for the ExperimentLogger class."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kasbench_controller.experiment.experiment_logger import ExperimentLogger


class TestExperimentLoggerCreation:
    """Tests for ExperimentLogger file creation and initialization."""

    def test_creates_log_file(self, tmp_path: Path) -> None:
        """Log file is created in the expected location."""
        logger = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="exp-001",
        )
        expected_path = tmp_path / "benchmarks" / "exp-001" / "experiment.log"
        assert expected_path.exists()
        logger.close()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Parent directories are created if they don't exist."""
        logger = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="deep-run",
        )
        assert (tmp_path / "benchmarks" / "deep-run").is_dir()
        logger.close()

    def test_log_path_property(self, tmp_path: Path) -> None:
        """log_path property returns the correct file path."""
        logger = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="exp-002",
        )
        expected = tmp_path / "benchmarks" / "exp-002" / "experiment.log"
        assert logger.log_path == expected
        logger.close()

    def test_exits_on_unwritable_path(self, tmp_path: Path) -> None:
        """Exits with non-zero code if log file cannot be created."""
        # Create a file where the directory should be, making mkdir fail
        blocker = tmp_path / "benchmarks"
        blocker.write_text("blocker")

        with pytest.raises(SystemExit):
            ExperimentLogger(
                working_directory=tmp_path,
                run_identifier="exp-fail",
            )

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        """Appends to an existing log file without overwriting."""
        log_dir = tmp_path / "benchmarks" / "exp-003"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "experiment.log"
        log_file.write_text('{"existing": "entry"}\n')

        logger = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="exp-003",
        )
        logger.log_step_complete(
            trial_identifier="trial0001",
            autoscaler="hpa",
            step="init",
        )
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert lines[0] == '{"existing": "entry"}'


class TestExperimentLoggerStepEvents:
    """Tests for step-level log entries."""

    @pytest.fixture
    def logger(self, tmp_path: Path) -> ExperimentLogger:
        """Create a logger for testing."""
        lg = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="test-run",
        )
        yield lg
        lg.close()

    @pytest.fixture
    def log_file(self, tmp_path: Path) -> Path:
        """Return the path to the log file."""
        return tmp_path / "benchmarks" / "test-run" / "experiment.log"

    def _read_entries(self, log_file: Path) -> list[dict]:
        """Read all JSON entries from the log file."""
        lines = log_file.read_text().strip().split("\n")
        return [json.loads(line) for line in lines if line]

    def test_log_step_start(self, logger: ExperimentLogger, log_file: Path) -> None:
        """log_step_start writes entry with correct fields."""
        logger.log_step_start(
            trial_identifier="trial0001",
            autoscaler="keda",
            step="build-infrastructure",
        )

        entries = self._read_entries(log_file)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["level"] == "info"
        assert entry["event"] == "step"
        assert entry["trial_identifier"] == "trial0001"
        assert entry["autoscaler"] == "keda"
        assert entry["step"] == "build-infrastructure"
        assert entry["outcome"] == "started"
        assert "timestamp" in entry

    def test_log_step_complete(self, logger: ExperimentLogger, log_file: Path) -> None:
        """log_step_complete writes entry with outcome=success."""
        logger.log_step_complete(
            trial_identifier="trial0002",
            autoscaler="hpa",
            step="init",
        )

        entries = self._read_entries(log_file)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["level"] == "info"
        assert entry["event"] == "step"
        assert entry["trial_identifier"] == "trial0002"
        assert entry["autoscaler"] == "hpa"
        assert entry["step"] == "init"
        assert entry["outcome"] == "success"
        assert "timestamp" in entry

    def test_log_step_failed_minimal(
        self, logger: ExperimentLogger, log_file: Path
    ) -> None:
        """log_step_failed writes error entry with required fields."""
        logger.log_step_failed(
            trial_identifier="trial0001",
            autoscaler="keda",
            step="initialize-runner",
            error="Health check timed out after 30s",
        )

        entries = self._read_entries(log_file)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["level"] == "error"
        assert entry["event"] == "step"
        assert entry["trial_identifier"] == "trial0001"
        assert entry["autoscaler"] == "keda"
        assert entry["step"] == "initialize-runner"
        assert entry["outcome"] == "failure"
        assert entry["error"] == "Health check timed out after 30s"
        assert "stderr" not in entry
        assert "return_code" not in entry
        assert "traceback" not in entry

    def test_log_step_failed_with_all_details(
        self, logger: ExperimentLogger, log_file: Path
    ) -> None:
        """log_step_failed includes optional error details when provided."""
        logger.log_step_failed(
            trial_identifier="trial0003",
            autoscaler="vpa",
            step="build-infrastructure",
            error="Tofu apply failed",
            stderr="Error: resource already exists",
            return_code=1,
            traceback="Traceback (most recent call last):\n  ...",
        )

        entries = self._read_entries(log_file)
        entry = entries[0]
        assert entry["stderr"] == "Error: resource already exists"
        assert entry["return_code"] == 1
        assert entry["traceback"] == "Traceback (most recent call last):\n  ..."

    def test_timestamp_format_iso8601(
        self, logger: ExperimentLogger, log_file: Path
    ) -> None:
        """Timestamp is in ISO 8601 UTC format."""
        import re

        logger.log_step_complete(
            trial_identifier="trial0001",
            autoscaler="hpa",
            step="init",
        )

        entries = self._read_entries(log_file)
        timestamp = entries[0]["timestamp"]
        # Match ISO 8601 format: YYYY-MM-DDTHH:MM:SSZ
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
        assert re.match(pattern, timestamp), f"Timestamp '{timestamp}' not ISO 8601 UTC"


class TestExperimentLoggerTrialEvents:
    """Tests for trial-level log entries."""

    @pytest.fixture
    def logger(self, tmp_path: Path) -> ExperimentLogger:
        """Create a logger for testing."""
        lg = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="test-run",
        )
        yield lg
        lg.close()

    @pytest.fixture
    def log_file(self, tmp_path: Path) -> Path:
        """Return the path to the log file."""
        return tmp_path / "benchmarks" / "test-run" / "experiment.log"

    def _read_entries(self, log_file: Path) -> list[dict]:
        """Read all JSON entries from the log file."""
        lines = log_file.read_text().strip().split("\n")
        return [json.loads(line) for line in lines if line]

    def test_log_trial_start(self, logger: ExperimentLogger, log_file: Path) -> None:
        """log_trial_start writes entry with trial context."""
        logger.log_trial_start(
            trial_identifier="trial0001",
            autoscaler="keda",
            sequence_number=1,
            total_trials=12,
        )

        entries = self._read_entries(log_file)
        entry = entries[0]
        assert entry["level"] == "info"
        assert entry["event"] == "trial"
        assert entry["trial_identifier"] == "trial0001"
        assert entry["autoscaler"] == "keda"
        assert entry["outcome"] == "started"
        assert entry["sequence_number"] == 1
        assert entry["total_trials"] == 12

    def test_log_trial_complete(
        self, logger: ExperimentLogger, log_file: Path
    ) -> None:
        """log_trial_complete writes entry with outcome=success."""
        logger.log_trial_complete(
            trial_identifier="trial0001",
            autoscaler="hpa",
        )

        entries = self._read_entries(log_file)
        entry = entries[0]
        assert entry["level"] == "info"
        assert entry["event"] == "trial"
        assert entry["trial_identifier"] == "trial0001"
        assert entry["autoscaler"] == "hpa"
        assert entry["outcome"] == "success"

    def test_log_trial_aborted(
        self, logger: ExperimentLogger, log_file: Path
    ) -> None:
        """log_trial_aborted writes error entry with abort details."""
        logger.log_trial_aborted(
            trial_identifier="trial0005",
            autoscaler="none",
            failed_step="shutdown",
            error="SSH connection refused",
        )

        entries = self._read_entries(log_file)
        entry = entries[0]
        assert entry["level"] == "error"
        assert entry["event"] == "trial"
        assert entry["trial_identifier"] == "trial0005"
        assert entry["autoscaler"] == "none"
        assert entry["step"] == "shutdown"
        assert entry["outcome"] == "aborted"
        assert entry["error"] == "SSH connection refused"


class TestExperimentLoggerExperimentEvents:
    """Tests for experiment-level log entries."""

    @pytest.fixture
    def logger(self, tmp_path: Path) -> ExperimentLogger:
        """Create a logger for testing."""
        lg = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="test-run",
        )
        yield lg
        lg.close()

    @pytest.fixture
    def log_file(self, tmp_path: Path) -> Path:
        """Return the path to the log file."""
        return tmp_path / "benchmarks" / "test-run" / "experiment.log"

    def _read_entries(self, log_file: Path) -> list[dict]:
        """Read all JSON entries from the log file."""
        lines = log_file.read_text().strip().split("\n")
        return [json.loads(line) for line in lines if line]

    def test_log_experiment_start(
        self, logger: ExperimentLogger, log_file: Path
    ) -> None:
        """log_experiment_start writes entry with experiment metadata."""
        logger.log_experiment_start(
            run_identifier="exp-2024-001",
            total_trials=12,
            effective_seed=42,
        )

        entries = self._read_entries(log_file)
        entry = entries[0]
        assert entry["level"] == "info"
        assert entry["event"] == "experiment"
        assert entry["outcome"] == "started"
        assert entry["run_identifier"] == "exp-2024-001"
        assert entry["total_trials"] == 12
        assert entry["effective_seed"] == 42

    def test_log_experiment_complete(
        self, logger: ExperimentLogger, log_file: Path
    ) -> None:
        """log_experiment_complete writes entry with outcome=success."""
        logger.log_experiment_complete(run_identifier="exp-2024-001")

        entries = self._read_entries(log_file)
        entry = entries[0]
        assert entry["level"] == "info"
        assert entry["event"] == "experiment"
        assert entry["outcome"] == "success"
        assert entry["run_identifier"] == "exp-2024-001"


class TestExperimentLoggerJsonLines:
    """Tests for JSON Lines format compliance."""

    def test_multiple_entries_are_separate_lines(self, tmp_path: Path) -> None:
        """Each log entry is on its own line (JSON Lines format)."""
        logger = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="jsonl-test",
        )
        log_file = tmp_path / "benchmarks" / "jsonl-test" / "experiment.log"

        logger.log_step_start("trial0001", "hpa", "init")
        logger.log_step_complete("trial0001", "hpa", "init")
        logger.log_step_start("trial0001", "hpa", "build-infrastructure")
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 3
        # Each line is valid JSON
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_entries_contain_required_fields(self, tmp_path: Path) -> None:
        """Every entry has timestamp, level, event, trial_identifier, autoscaler, step."""
        logger = ExperimentLogger(
            working_directory=tmp_path,
            run_identifier="fields-test",
        )
        log_file = tmp_path / "benchmarks" / "fields-test" / "experiment.log"

        logger.log_step_complete("trial0001", "keda", "init")
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        entry = json.loads(lines[0])
        required_fields = {"timestamp", "level", "event", "trial_identifier", "autoscaler", "step", "outcome"}
        assert required_fields.issubset(entry.keys())
