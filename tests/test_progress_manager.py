"""Unit tests for ProgressManager."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from kasbench_controller.experiment.config import ExperimentConfig
from kasbench_controller.experiment.models import ExperimentProgress, TrialAssignment
from kasbench_controller.experiment.progress import TRIAL_STEPS, ProgressManager


def _make_config(**overrides) -> ExperimentConfig:
    """Create a test ExperimentConfig with sensible defaults."""
    defaults = {
        "run_identifier": "test-run-001",
        "trial_prefix": "trial",
        "autoscalers": ["hpa", "vpa"],
        "trials_per_autoscaler": 2,
        "run_duration": 30,
        "working_directory": Path("/tmp/benchmarks"),
        "s3_bucket": "test-bucket",
        "aws_region": "us-east-1",
        "var_files": ["us-east-1.tfvars"],
        "variables": ["key1=val1"],
        "auto_approve": True,
        "runner_version": "0.2.6",
        "health_timeout": 30,
        "rollout_timeout": 600,
        "cluster_cidr_range": "10.244.0.0/16",
        "role_params": None,
        "random_seed": 42,
        "ebs_wait": 300,
        "rerun_from_failed": False,
        "halt_on_error": False,
    }
    defaults.update(overrides)
    return ExperimentConfig(**defaults)


def _make_schedule() -> list[TrialAssignment]:
    """Create a simple 4-trial test schedule."""
    return [
        TrialAssignment(trial_identifier="trial0001", autoscaler="hpa", sequence_number=1),
        TrialAssignment(trial_identifier="trial0002", autoscaler="vpa", sequence_number=2),
        TrialAssignment(trial_identifier="trial0003", autoscaler="hpa", sequence_number=3),
        TrialAssignment(trial_identifier="trial0004", autoscaler="vpa", sequence_number=4),
    ]


def _make_progress_data(trial_results=None) -> dict:
    """Create a progress file JSON structure."""
    config = _make_config()
    return {
        "version": 1,
        "parameters": ProgressManager._config_to_params(config),
        "effective_seed": 42,
        "schedule": [
            {"trial_identifier": "trial0001", "autoscaler": "hpa"},
            {"trial_identifier": "trial0002", "autoscaler": "vpa"},
            {"trial_identifier": "trial0003", "autoscaler": "hpa"},
            {"trial_identifier": "trial0004", "autoscaler": "vpa"},
        ],
        "trial_results": trial_results or [],
    }


class TestProgressManagerInit:
    """Tests for ProgressManager initialization."""

    def test_s3_key_constructed_correctly(self):
        pm = ProgressManager("my-bucket", "us-west-2", "exp-001")
        assert pm._s3_key == "exp-001/experiment-progress.json"
        assert pm._s3_bucket == "my-bucket"
        assert pm._aws_region == "us-west-2"

    def test_progress_initially_none(self):
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        assert pm.progress is None


class TestLoadOrCreate:
    """Tests for load_or_create method."""

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_creates_new_progress_when_no_existing_file(self, mock_boto3):
        """When S3 has no progress file, a new one is created."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        # Simulate NoSuchKey error (file not found)
        error_response = {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}
        mock_client.get_object.side_effect = ClientError(error_response, "GetObject")

        pm = ProgressManager("test-bucket", "us-east-1", "test-run")
        pm._s3_client = mock_client

        config = _make_config()
        schedule = _make_schedule()
        result = pm.load_or_create(config, schedule, effective_seed=42)

        assert isinstance(result, ExperimentProgress)
        assert result.version == 1
        assert result.effective_seed == 42
        assert len(result.schedule) == 4
        assert result.trial_results == []
        assert result.parameters["run_identifier"] == "test-run-001"
        # Should have uploaded the new progress
        mock_client.put_object.assert_called_once()

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_loads_existing_progress_from_s3(self, mock_boto3):
        """When S3 has a valid progress file, it is loaded."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        progress_data = _make_progress_data()
        body_mock = MagicMock()
        body_mock.read.return_value = json.dumps(progress_data).encode("utf-8")
        mock_client.get_object.return_value = {"Body": body_mock}

        pm = ProgressManager("test-bucket", "us-east-1", "test-run")
        pm._s3_client = mock_client

        config = _make_config()
        schedule = _make_schedule()
        result = pm.load_or_create(config, schedule, effective_seed=42)

        assert isinstance(result, ExperimentProgress)
        assert result.effective_seed == 42
        assert len(result.schedule) == 4

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_malformed_json_exits_with_error(self, mock_boto3):
        """When S3 progress file has malformed JSON, exits with clear error."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        body_mock = MagicMock()
        body_mock.read.return_value = b"{ invalid json !!!"
        mock_client.get_object.return_value = {"Body": body_mock}

        pm = ProgressManager("test-bucket", "us-east-1", "test-run")
        pm._s3_client = mock_client

        config = _make_config()
        schedule = _make_schedule()

        with pytest.raises(SystemExit) as exc_info:
            pm.load_or_create(config, schedule, effective_seed=42)

        error_msg = str(exc_info.value)
        assert "malformed JSON" in error_msg
        assert "test-bucket" in error_msg


class TestRecordStepResult:
    """Tests for record_step_result method."""

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_records_success_step(self, mock_boto3):
        """Records a successful step and persists to S3."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._s3_client = mock_client
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[{"trial_identifier": "trial0001", "autoscaler": "hpa"}],
            trial_results=[],
            effective_seed=42,
        )

        pm.record_step_result("trial0001", "init", "success")

        assert len(pm._progress.trial_results) == 1
        trial = pm._progress.trial_results[0]
        assert trial["trial_identifier"] == "trial0001"
        assert trial["autoscaler"] == "hpa"
        assert len(trial["steps"]) == 1
        assert trial["steps"][0]["step"] == "init"
        assert trial["steps"][0]["status"] == "success"
        assert "timestamp" in trial["steps"][0]
        assert "error" not in trial["steps"][0]

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_records_failed_step_with_error(self, mock_boto3):
        """Records a failed step with error message."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._s3_client = mock_client
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[{"trial_identifier": "trial0001", "autoscaler": "keda"}],
            trial_results=[],
            effective_seed=42,
        )

        pm.record_step_result("trial0001", "build-infrastructure", "failed", error="Timeout")

        trial = pm._progress.trial_results[0]
        step = trial["steps"][0]
        assert step["status"] == "failed"
        assert step["error"] == "Timeout"

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_appends_to_existing_trial_entry(self, mock_boto3):
        """Adds steps to an existing trial result entry."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._s3_client = mock_client
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[{"trial_identifier": "trial0001", "autoscaler": "hpa"}],
            trial_results=[
                {
                    "trial_identifier": "trial0001",
                    "autoscaler": "hpa",
                    "steps": [{"step": "init", "status": "success", "timestamp": "2024-01-01T00:00:00Z"}],
                }
            ],
            effective_seed=42,
        )

        pm.record_step_result("trial0001", "build-infrastructure", "success")

        assert len(pm._progress.trial_results) == 1
        assert len(pm._progress.trial_results[0]["steps"]) == 2


class TestFindResumePoint:
    """Tests for find_resume_point method."""

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_no_progress_returns_start(self, mock_boto3):
        """No progress loaded returns (0, None)."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        assert pm.find_resume_point(rerun_from_failed=False) == (0, None)

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_no_results_returns_first_trial(self, mock_boto3):
        """Empty trial_results returns first trial start."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
                {"trial_identifier": "trial0002", "autoscaler": "vpa"},
            ],
            trial_results=[],
            effective_seed=42,
        )

        assert pm.find_resume_point(rerun_from_failed=False) == (0, None)

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_first_trial_complete_resumes_at_second(self, mock_boto3):
        """When first trial is fully complete, resume at second."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        all_steps_success = [
            {"step": s, "status": "success", "timestamp": "2024-01-01T00:00:00Z"}
            for s in TRIAL_STEPS
        ]
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
                {"trial_identifier": "trial0002", "autoscaler": "vpa"},
            ],
            trial_results=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa", "steps": all_steps_success},
            ],
            effective_seed=42,
        )

        assert pm.find_resume_point(rerun_from_failed=False) == (1, None)

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_rerun_from_failed_finds_failed_step(self, mock_boto3):
        """With rerun_from_failed=True, returns the failed step name."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
            ],
            trial_results=[
                {
                    "trial_identifier": "trial0001",
                    "autoscaler": "hpa",
                    "steps": [
                        {"step": "init", "status": "success", "timestamp": "2024-01-01T00:00:00Z"},
                        {"step": "build-infrastructure", "status": "failed", "timestamp": "2024-01-01T00:05:00Z"},
                    ],
                },
            ],
            effective_seed=42,
        )

        assert pm.find_resume_point(rerun_from_failed=True) == (0, "build-infrastructure")

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_rerun_from_failed_finds_missing_step(self, mock_boto3):
        """With rerun_from_failed=True, finds first unrecorded step."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
            ],
            trial_results=[
                {
                    "trial_identifier": "trial0001",
                    "autoscaler": "hpa",
                    "steps": [
                        {"step": "init", "status": "success", "timestamp": "2024-01-01T00:00:00Z"},
                        {"step": "build-infrastructure", "status": "success", "timestamp": "2024-01-01T00:05:00Z"},
                    ],
                },
            ],
            effective_seed=42,
        )

        # Should find "wait" as the first unrecorded step
        assert pm.find_resume_point(rerun_from_failed=True) == (0, "wait")

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_all_trials_complete(self, mock_boto3):
        """When all trials complete, returns index past schedule."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        all_steps = [
            {"step": s, "status": "success", "timestamp": "2024-01-01T00:00:00Z"}
            for s in TRIAL_STEPS
        ]
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
                {"trial_identifier": "trial0002", "autoscaler": "vpa"},
            ],
            trial_results=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa", "steps": all_steps},
                {"trial_identifier": "trial0002", "autoscaler": "vpa", "steps": all_steps},
            ],
            effective_seed=42,
        )

        assert pm.find_resume_point(rerun_from_failed=False) == (2, None)


class TestValidateParameters:
    """Tests for validate_parameters method."""

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_matching_params_returns_empty(self, mock_boto3):
        """Identical configs return no mismatches."""
        config = _make_config()
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters=ProgressManager._config_to_params(config),
            schedule=[],
            trial_results=[],
            effective_seed=42,
        )

        assert pm.validate_parameters(config) == []

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_different_run_duration_detected(self, mock_boto3):
        """Detects mismatch in run_duration."""
        stored_config = _make_config(run_duration=30)
        current_config = _make_config(run_duration=60)

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters=ProgressManager._config_to_params(stored_config),
            schedule=[],
            trial_results=[],
            effective_seed=42,
        )

        mismatches = pm.validate_parameters(current_config)
        assert "run_duration" in mismatches

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_halt_on_error_excluded_from_comparison(self, mock_boto3):
        """halt_on_error differences do NOT cause mismatch."""
        config_a = _make_config(halt_on_error=False)
        config_b = _make_config(halt_on_error=True)

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters=ProgressManager._config_to_params(config_a),
            schedule=[],
            trial_results=[],
            effective_seed=42,
        )

        # halt_on_error is excluded from params, so no mismatch
        assert pm.validate_parameters(config_b) == []

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_rerun_from_failed_excluded_from_comparison(self, mock_boto3):
        """rerun_from_failed differences do NOT cause mismatch."""
        config_a = _make_config(rerun_from_failed=False)
        config_b = _make_config(rerun_from_failed=True)

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters=ProgressManager._config_to_params(config_a),
            schedule=[],
            trial_results=[],
            effective_seed=42,
        )

        assert pm.validate_parameters(config_b) == []

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_multiple_mismatches_all_reported(self, mock_boto3):
        """All differing parameters are reported."""
        stored = _make_config(run_duration=30, runner_version="0.2.6")
        current = _make_config(run_duration=60, runner_version="0.3.0")

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters=ProgressManager._config_to_params(stored),
            schedule=[],
            trial_results=[],
            effective_seed=42,
        )

        mismatches = pm.validate_parameters(current)
        assert "run_duration" in mismatches
        assert "runner_version" in mismatches


class TestIsComplete:
    """Tests for is_complete method."""

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_no_progress_returns_false(self, mock_boto3):
        """No progress loaded returns False."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        assert pm.is_complete() is False

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_empty_schedule_returns_true(self, mock_boto3):
        """Empty schedule is trivially complete."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[],
            trial_results=[],
            effective_seed=42,
        )
        assert pm.is_complete() is True

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_all_trials_success_returns_true(self, mock_boto3):
        """All trials with all steps success returns True."""
        all_steps = [
            {"step": s, "status": "success", "timestamp": "2024-01-01T00:00:00Z"}
            for s in TRIAL_STEPS
        ]
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
                {"trial_identifier": "trial0002", "autoscaler": "vpa"},
            ],
            trial_results=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa", "steps": all_steps},
                {"trial_identifier": "trial0002", "autoscaler": "vpa", "steps": all_steps},
            ],
            effective_seed=42,
        )
        assert pm.is_complete() is True

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_incomplete_trial_returns_false(self, mock_boto3):
        """Missing a trial result returns False."""
        all_steps = [
            {"step": s, "status": "success", "timestamp": "2024-01-01T00:00:00Z"}
            for s in TRIAL_STEPS
        ]
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
                {"trial_identifier": "trial0002", "autoscaler": "vpa"},
            ],
            trial_results=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa", "steps": all_steps},
            ],
            effective_seed=42,
        )
        assert pm.is_complete() is False

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_trial_with_failed_step_returns_false(self, mock_boto3):
        """A trial with a failed step is not complete."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
            ],
            trial_results=[
                {
                    "trial_identifier": "trial0001",
                    "autoscaler": "hpa",
                    "steps": [
                        {"step": "init", "status": "success", "timestamp": "2024-01-01T00:00:00Z"},
                        {"step": "build-infrastructure", "status": "failed", "timestamp": "2024-01-01T00:05:00Z"},
                    ],
                },
            ],
            effective_seed=42,
        )
        assert pm.is_complete() is False

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_aborted_trials_excluded_from_completion(self, mock_boto3):
        """Aborted trials are skipped — only non-aborted trials must be complete."""
        all_steps = [
            {"step": s, "status": "success", "timestamp": "2024-01-01T00:00:00Z"}
            for s in TRIAL_STEPS
        ]
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
                {"trial_identifier": "trial0002", "autoscaler": "vpa"},
                {"trial_identifier": "trial0003", "autoscaler": "hpa"},
            ],
            trial_results=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa", "steps": all_steps},
                {
                    "trial_identifier": "trial0002",
                    "autoscaler": "vpa",
                    "status": "aborted",
                    "reason": "spot-interruption",
                    "aborted_at": "2024-01-01T00:10:00Z",
                    "steps": [
                        {"step": "build-infrastructure", "status": "success", "timestamp": "2024-01-01T00:05:00Z"},
                    ],
                },
                {"trial_identifier": "trial0003", "autoscaler": "hpa", "steps": all_steps},
            ],
            effective_seed=42,
        )
        # trial0002 is aborted, but trial0001 and trial0003 are complete => True
        assert pm.is_complete() is True

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_only_aborted_trials_incomplete_returns_true(self, mock_boto3):
        """When only aborted trials remain incomplete, is_complete returns True."""
        all_steps = [
            {"step": s, "status": "success", "timestamp": "2024-01-01T00:00:00Z"}
            for s in TRIAL_STEPS
        ]
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
                {"trial_identifier": "trial0002", "autoscaler": "vpa"},
            ],
            trial_results=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa", "steps": all_steps},
                {
                    "trial_identifier": "trial0002",
                    "autoscaler": "vpa",
                    "status": "aborted",
                    "reason": "spot-interruption",
                    "aborted_at": "2024-01-01T00:10:00Z",
                    "steps": [],
                },
            ],
            effective_seed=42,
        )
        assert pm.is_complete() is True

    @patch("kasbench_controller.experiment.progress.boto3")
    def test_non_aborted_incomplete_trial_returns_false(self, mock_boto3):
        """When a non-aborted trial is incomplete, is_complete returns False even if others are aborted."""
        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[
                {"trial_identifier": "trial0001", "autoscaler": "hpa"},
                {"trial_identifier": "trial0002", "autoscaler": "vpa"},
                {"trial_identifier": "trial0003", "autoscaler": "hpa"},
            ],
            trial_results=[
                {
                    "trial_identifier": "trial0001",
                    "autoscaler": "hpa",
                    "steps": [
                        {"step": "build-infrastructure", "status": "success", "timestamp": "2024-01-01T00:00:00Z"},
                    ],
                },
                {
                    "trial_identifier": "trial0002",
                    "autoscaler": "vpa",
                    "status": "aborted",
                    "reason": "spot-interruption",
                    "aborted_at": "2024-01-01T00:10:00Z",
                    "steps": [],
                },
            ],
            effective_seed=42,
        )
        # trial0001 is incomplete (not all steps success), trial0003 has no results
        assert pm.is_complete() is False


class TestS3UploadRetry:
    """Tests for S3 upload retry behavior."""

    @patch("kasbench_controller.experiment.progress.time.sleep")
    @patch("kasbench_controller.experiment.progress.boto3")
    def test_retries_on_failure(self, mock_boto3, mock_sleep):
        """Upload retries up to 3 times with 5s delay."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        # Fail twice, succeed on third
        mock_client.put_object.side_effect = [
            ClientError({"Error": {"Code": "500", "Message": "Internal"}}, "PutObject"),
            ClientError({"Error": {"Code": "500", "Message": "Internal"}}, "PutObject"),
            None,  # success
        ]

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._s3_client = mock_client
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[],
            trial_results=[],
            effective_seed=42,
        )

        pm._upload_progress()

        assert mock_client.put_object.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(5)

    @patch("kasbench_controller.experiment.progress.time.sleep")
    @patch("kasbench_controller.experiment.progress.boto3")
    def test_continues_after_exhausting_retries(self, mock_boto3, mock_sleep):
        """After 3 failures, logs error and continues (does not raise)."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        mock_client.put_object.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "Internal"}}, "PutObject"
        )

        pm = ProgressManager("bucket", "us-east-1", "run-1")
        pm._s3_client = mock_client
        pm._progress = ExperimentProgress(
            parameters={},
            schedule=[],
            trial_results=[],
            effective_seed=42,
        )

        # Should not raise
        pm._upload_progress()

        assert mock_client.put_object.call_count == 3
        assert mock_sleep.call_count == 2


class TestConfigToParams:
    """Tests for _config_to_params static method."""

    def test_excludes_halt_on_error(self):
        """halt_on_error is NOT in the params dict."""
        config = _make_config(halt_on_error=True)
        params = ProgressManager._config_to_params(config)
        assert "halt_on_error" not in params

    def test_excludes_rerun_from_failed(self):
        """rerun_from_failed is NOT in the params dict."""
        config = _make_config(rerun_from_failed=True)
        params = ProgressManager._config_to_params(config)
        assert "rerun_from_failed" not in params

    def test_excludes_random_seed(self):
        """random_seed is NOT in the params dict (effective_seed is stored separately)."""
        config = _make_config(random_seed=123)
        params = ProgressManager._config_to_params(config)
        assert "random_seed" not in params

    def test_includes_all_comparable_params(self):
        """All non-excluded params are present."""
        config = _make_config()
        params = ProgressManager._config_to_params(config)
        expected_keys = {
            "run_identifier", "trial_prefix", "autoscalers",
            "trials_per_autoscaler", "run_duration", "working_directory",
            "s3_bucket", "aws_region", "var_files", "variables",
            "auto_approve", "runner_version", "health_timeout",
            "rollout_timeout", "cluster_cidr_range", "role_params", "ebs_wait",
        }
        assert set(params.keys()) == expected_keys

    def test_working_directory_stored_as_string(self):
        """Path objects are converted to strings."""
        config = _make_config(working_directory=Path("/home/ubuntu/bench"))
        params = ProgressManager._config_to_params(config)
        assert params["working_directory"] == "/home/ubuntu/bench"
        assert isinstance(params["working_directory"], str)
