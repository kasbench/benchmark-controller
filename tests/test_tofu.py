"""Tests for the kasbench_controller.tofu module."""

from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import pytest

from kasbench_controller.tofu import TofuRunner, TofuResult
from kasbench_controller.exceptions import TofuError


@pytest.fixture
def working_dir(tmp_path: Path) -> Path:
    """Provide a temporary working directory for TofuRunner."""
    wd = tmp_path / "benchmark-infrastructure"
    wd.mkdir()
    (wd / "environments").mkdir()
    return wd


@pytest.fixture
def runner(working_dir: Path) -> TofuRunner:
    """Provide a TofuRunner instance."""
    return TofuRunner(working_dir=working_dir, dry_run=False)


@pytest.fixture
def dry_runner(working_dir: Path) -> TofuRunner:
    """Provide a TofuRunner instance in dry-run mode."""
    return TofuRunner(working_dir=working_dir, dry_run=True)


class TestTofuResult:
    """TofuResult dataclass stores command output."""

    def test_success_result(self) -> None:
        result = TofuResult(return_code=0, stdout="ok", stderr="", success=True)
        assert result.return_code == 0
        assert result.stdout == "ok"
        assert result.stderr == ""
        assert result.success is True

    def test_failure_result(self) -> None:
        result = TofuResult(return_code=1, stdout="", stderr="error", success=False)
        assert result.return_code == 1
        assert result.success is False


class TestResolveVarFile:
    """_resolve_var_file resolves filenames vs paths correctly."""

    def test_filename_only_resolves_to_environments(self, runner: TofuRunner, working_dir: Path) -> None:
        resolved = runner._resolve_var_file("prod.tfvars")
        assert resolved == working_dir / "environments" / "prod.tfvars"

    def test_relative_path_used_as_is(self, runner: TofuRunner) -> None:
        resolved = runner._resolve_var_file("configs/prod.tfvars")
        assert resolved == Path("configs/prod.tfvars")

    def test_absolute_path_used_as_is(self, runner: TofuRunner) -> None:
        resolved = runner._resolve_var_file("/etc/tofu/prod.tfvars")
        assert resolved == Path("/etc/tofu/prod.tfvars")

    def test_filename_with_dots_but_no_separator(self, runner: TofuRunner, working_dir: Path) -> None:
        resolved = runner._resolve_var_file("my.env.tfvars")
        assert resolved == working_dir / "environments" / "my.env.tfvars"


class TestBuildVarArgs:
    """_build_var_args produces correctly ordered arguments."""

    def test_var_files_first_then_vars_then_run_id(self, runner: TofuRunner, working_dir: Path) -> None:
        args = runner._build_var_args(
            var_files=["prod.tfvars"],
            variables=["region=us-east-1"],
            run_id="trial001",
        )
        expected_var_file = str(working_dir / "environments" / "prod.tfvars")
        assert args == [
            f"-var-file={expected_var_file}",
            "-var=region=us-east-1",
            "-var=run_id=trial001",
        ]

    def test_multiple_var_files_preserve_order(self, runner: TofuRunner, working_dir: Path) -> None:
        args = runner._build_var_args(
            var_files=["a.tfvars", "b.tfvars"],
            variables=[],
            run_id="run1",
        )
        env_dir = working_dir / "environments"
        assert args[0] == f"-var-file={env_dir / 'a.tfvars'}"
        assert args[1] == f"-var-file={env_dir / 'b.tfvars'}"
        assert args[2] == "-var=run_id=run1"

    def test_multiple_variables_preserve_order(self, runner: TofuRunner) -> None:
        args = runner._build_var_args(
            var_files=[],
            variables=["a=1", "b=2", "c=3"],
            run_id="trial",
        )
        assert args == ["-var=a=1", "-var=b=2", "-var=c=3", "-var=run_id=trial"]

    def test_empty_var_files_and_variables(self, runner: TofuRunner) -> None:
        args = runner._build_var_args(var_files=[], variables=[], run_id="x")
        assert args == ["-var=run_id=x"]

    def test_run_id_always_last(self, runner: TofuRunner, working_dir: Path) -> None:
        args = runner._build_var_args(
            var_files=["env.tfvars"],
            variables=["key=val"],
            run_id="myrun",
        )
        assert args[-1] == "-var=run_id=myrun"

    def test_path_var_file_not_resolved_to_environments(self, runner: TofuRunner) -> None:
        args = runner._build_var_args(
            var_files=["/absolute/path.tfvars"],
            variables=[],
            run_id="r1",
        )
        assert args[0] == "-var-file=/absolute/path.tfvars"


class TestInit:
    """TofuRunner.init() runs tofu init."""

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_init_success(self, mock_run: MagicMock, runner: TofuRunner, working_dir: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["tofu", "init"], returncode=0, stdout="Initialized", stderr=""
        )
        result = runner.init()
        assert result.success is True
        assert result.stdout == "Initialized"
        mock_run.assert_called_once_with(
            ["tofu", "init"],
            cwd=working_dir,
            capture_output=True,
            text=True,
        )

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_init_failure_raises_tofu_error(self, mock_run: MagicMock, runner: TofuRunner) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["tofu", "init"], returncode=1, stdout="", stderr="plugin error"
        )
        with pytest.raises(TofuError) as exc_info:
            runner.init()
        assert exc_info.value.return_code == 1
        assert exc_info.value.stderr == "plugin error"

    def test_init_dry_run(self, dry_runner: TofuRunner) -> None:
        result = dry_runner.init()
        assert result.success is True
        assert result.return_code == 0


class TestApply:
    """TofuRunner.apply() runs tofu apply with correct arguments."""

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_apply_with_auto_approve(self, mock_run: MagicMock, runner: TofuRunner, working_dir: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Apply complete", stderr=""
        )
        result = runner.apply(
            var_files=["prod.tfvars"],
            variables=["region=us-east-1"],
            run_id="trial001",
            auto_approve=True,
        )
        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["tofu", "apply"]
        assert "-auto-approve" in call_args

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_apply_without_auto_approve(self, mock_run: MagicMock, runner: TofuRunner) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        runner.apply(
            var_files=[],
            variables=[],
            run_id="trial001",
            auto_approve=False,
        )
        call_args = mock_run.call_args[0][0]
        assert "-auto-approve" not in call_args

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_apply_failure_raises_tofu_error(self, mock_run: MagicMock, runner: TofuRunner) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="apply failed"
        )
        with pytest.raises(TofuError) as exc_info:
            runner.apply(var_files=[], variables=[], run_id="t1", auto_approve=True)
        assert exc_info.value.return_code == 1
        assert "apply failed" in exc_info.value.stderr

    def test_apply_dry_run(self, dry_runner: TofuRunner) -> None:
        result = dry_runner.apply(
            var_files=["env.tfvars"],
            variables=["x=1"],
            run_id="trial",
            auto_approve=True,
        )
        assert result.success is True
        assert result.return_code == 0


class TestPlan:
    """TofuRunner.plan() runs tofu plan with correct arguments."""

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_plan_success(self, mock_run: MagicMock, runner: TofuRunner, working_dir: Path) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Plan: 3 to add", stderr=""
        )
        result = runner.plan(
            var_files=["prod.tfvars"],
            variables=["region=us-east-1"],
            run_id="trial001",
        )
        assert result.success is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["tofu", "plan"]
        env_path = str(working_dir / "environments" / "prod.tfvars")
        assert f"-var-file={env_path}" in call_args
        assert "-var=region=us-east-1" in call_args
        assert "-var=run_id=trial001" in call_args

    def test_plan_dry_run(self, dry_runner: TofuRunner) -> None:
        result = dry_runner.plan(var_files=[], variables=[], run_id="t1")
        assert result.success is True


class TestOutputJson:
    """TofuRunner.output_json() runs tofu output -json and parses result."""

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_output_json_success(self, mock_run: MagicMock, runner: TofuRunner, working_dir: Path) -> None:
        output_data = {
            "benchmark_runner": {
                "value": {"public_ip": "1.2.3.4"},
                "type": "object",
                "sensitive": False,
            },
            "ssh_key_pair_name": {
                "value": "kasbench-trial001",
                "type": "string",
                "sensitive": False,
            },
        }
        import json
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(output_data), stderr=""
        )
        result = runner.output_json()
        assert result == output_data
        call_args = mock_run.call_args[0][0]
        assert call_args == ["tofu", "output", "-json"]

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_output_json_command_failure(self, mock_run: MagicMock, runner: TofuRunner) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no state"
        )
        with pytest.raises(TofuError):
            runner.output_json()

    @patch("kasbench_controller.tofu.subprocess.run")
    def test_output_json_invalid_json(self, mock_run: MagicMock, runner: TofuRunner) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not valid json{{{", stderr=""
        )
        with pytest.raises(TofuError) as exc_info:
            runner.output_json()
        assert "Failed to parse" in str(exc_info.value)

    def test_output_json_dry_run(self, dry_runner: TofuRunner) -> None:
        result = dry_runner.output_json()
        assert result == {}
