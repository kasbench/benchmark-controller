"""Tests for the structured logging module."""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

from kasbench_controller.logging import configure_logging, log_dry_run, log_step


class TestConfigureLogging:
    """Tests for configure_logging function."""

    def test_returns_bound_logger(self):
        """configure_logging returns a structlog BoundLogger."""
        logger = configure_logging()
        assert logger is not None

    def test_stdout_output_is_json_lines(self, capsys):
        """Log entries emitted to stdout are valid JSON Lines."""
        logger = configure_logging()
        logger.info("test_event", key="value")

        captured = capsys.readouterr()
        line = captured.out.strip()
        parsed = json.loads(line)
        assert parsed["event"] == "test_event"
        assert parsed["key"] == "value"

    def test_log_file_receives_entries(self, tmp_path):
        """When log_file is provided, entries are written to the file."""
        log_path = tmp_path / "test.log"
        logger = configure_logging(log_file=log_path)
        logger.info("file_event", data="hello")

        content = log_path.read_text().strip()
        parsed = json.loads(content)
        assert parsed["event"] == "file_event"
        assert parsed["data"] == "hello"

    def test_log_file_and_stdout_both_receive_entries(self, tmp_path, capsys):
        """Entries go to both stdout and the log file."""
        log_path = tmp_path / "test.log"
        logger = configure_logging(log_file=log_path)
        logger.info("dual_event")

        # Check stdout
        captured = capsys.readouterr()
        stdout_parsed = json.loads(captured.out.strip())
        assert stdout_parsed["event"] == "dual_event"

        # Check file
        file_parsed = json.loads(log_path.read_text().strip())
        assert file_parsed["event"] == "dual_event"

    def test_log_file_parent_directories_created(self, tmp_path):
        """Parent directories for log_file are created if they don't exist."""
        log_path = tmp_path / "nested" / "dir" / "test.log"
        logger = configure_logging(log_file=log_path)
        logger.info("nested_event")

        assert log_path.exists()
        parsed = json.loads(log_path.read_text().strip())
        assert parsed["event"] == "nested_event"

    def test_invalid_log_file_exits_with_code_1(self, tmp_path):
        """If log file cannot be created, exit with code 1 and stderr message."""
        # Use a path that cannot be created (file as parent directory)
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file")
        bad_path = blocker / "impossible.log"

        with pytest.raises(SystemExit) as exc_info:
            configure_logging(log_file=bad_path)

        assert exc_info.value.code == 1

    def test_invalid_log_file_writes_to_stderr(self, tmp_path, capsys):
        """If log file cannot be created, error message goes to stderr."""
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file")
        bad_path = blocker / "impossible.log"

        with pytest.raises(SystemExit):
            configure_logging(log_file=bad_path)

        captured = capsys.readouterr()
        assert "Cannot create or write to log file" in captured.err
        assert str(bad_path) in captured.err

    def test_dry_run_binds_flag_to_logger(self, capsys):
        """When dry_run=True, the logger includes dry_run=True in entries."""
        logger = configure_logging(dry_run=True)
        logger.info("dry_event")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["dry_run"] is True

    def test_entries_include_timestamp(self, capsys):
        """Each log entry includes an ISO 8601 UTC timestamp."""
        logger = configure_logging()
        logger.info("ts_event")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "timestamp" in parsed
        # Verify ISO 8601 format ending in Z
        assert parsed["timestamp"].endswith("Z")
        assert "T" in parsed["timestamp"]

    def test_entries_include_level(self, capsys):
        """Each log entry includes a level field."""
        logger = configure_logging()
        logger.info("level_event")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["level"] == "info"


class TestLogStep:
    """Tests for log_step function."""

    def test_emits_step_and_outcome(self, capsys):
        """log_step emits entry with step and outcome fields."""
        logger = configure_logging()
        log_step(logger, "create_directory", "success")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["step"] == "create_directory"
        assert parsed["outcome"] == "success"

    def test_includes_extra_kwargs(self, capsys):
        """log_step passes additional kwargs into the log entry."""
        logger = configure_logging()
        log_step(logger, "tofu_apply", "failure", error="Exit code 1", path="/tmp")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["step"] == "tofu_apply"
        assert parsed["outcome"] == "failure"
        assert parsed["error"] == "Exit code 1"
        assert parsed["path"] == "/tmp"

    def test_failure_outcome_uses_error_level(self, capsys):
        """log_step with outcome='failure' logs at error level."""
        logger = configure_logging()
        log_step(logger, "bad_step", "failure")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["level"] == "error"

    def test_success_outcome_uses_info_level(self, capsys):
        """log_step with outcome='success' logs at info level."""
        logger = configure_logging()
        log_step(logger, "good_step", "success")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["level"] == "info"

    def test_includes_timestamp(self, capsys):
        """log_step entries include ISO 8601 UTC timestamp."""
        logger = configure_logging()
        log_step(logger, "timed_step", "success")

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "timestamp" in parsed
        assert parsed["timestamp"].endswith("Z")


class TestLogDryRun:
    """Tests for log_dry_run function."""

    def test_emits_operation_and_details(self, capsys):
        """log_dry_run emits entry with operation and detail fields."""
        logger = configure_logging()
        log_dry_run(logger, "create_directory", {"path": "/data/run001"})

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["event"] == "dry_run"
        assert parsed["operation"] == "create_directory"
        assert parsed["path"] == "/data/run001"

    def test_includes_timestamp(self, capsys):
        """log_dry_run entries include ISO 8601 UTC timestamp."""
        logger = configure_logging()
        log_dry_run(logger, "download_repo", {"url": "https://example.com"})

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "timestamp" in parsed
        assert parsed["timestamp"].endswith("Z")

    def test_multiple_detail_fields(self, capsys):
        """log_dry_run spreads all detail fields into the log entry."""
        logger = configure_logging()
        log_dry_run(
            logger,
            "tofu_apply",
            {"cwd": "/trial/infra", "auto_approve": True, "var_count": 3},
        )

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["cwd"] == "/trial/infra"
        assert parsed["auto_approve"] is True
        assert parsed["var_count"] == 3
