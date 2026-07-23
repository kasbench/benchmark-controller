"""Tests for the kasbench_controller.models module."""

import json
from pathlib import Path

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.models import (
    RunContext,
    TrialContext,
    TrialConfig,
    TofuOutputs,
    load_trial_config,
    save_trial_config,
)


class TestRunContext:
    """RunContext computes run_directory and db_path from inputs."""

    def test_run_directory_is_working_dir_joined_with_identifier(self) -> None:
        rc = RunContext(working_directory=Path("/data"), run_identifier="run001")
        assert rc.run_directory == Path("/data/run001")

    def test_db_path_is_run_directory_joined_with_benchmark_db(self) -> None:
        rc = RunContext(working_directory=Path("/data"), run_identifier="run001")
        assert rc.db_path == Path("/data/run001/benchmark.db")

    def test_preserves_working_directory(self) -> None:
        wd = Path("/tmp/bench")
        rc = RunContext(working_directory=wd, run_identifier="exp42")
        assert rc.working_directory == wd

    def test_preserves_run_identifier(self) -> None:
        rc = RunContext(working_directory=Path("/x"), run_identifier="my-run")
        assert rc.run_identifier == "my-run"

    def test_nested_working_directory(self) -> None:
        rc = RunContext(working_directory=Path("/a/b/c/d"), run_identifier="r1")
        assert rc.run_directory == Path("/a/b/c/d/r1")
        assert rc.db_path == Path("/a/b/c/d/r1/benchmark.db")


class TestTrialContext:
    """TrialContext computes trial paths from RunContext."""

    def _make_run_context(self) -> RunContext:
        return RunContext(working_directory=Path("/data"), run_identifier="run001")

    def test_trial_directory_is_run_dir_joined_with_trial_id(self) -> None:
        rc = self._make_run_context()
        tc = TrialContext(run_context=rc, trial_identifier="trial001", autoscaler="keda")
        assert tc.trial_directory == Path("/data/run001/trial001")

    def test_tofu_directory_is_trial_dir_joined_with_benchmark_infrastructure(self) -> None:
        rc = self._make_run_context()
        tc = TrialContext(run_context=rc, trial_identifier="trial001", autoscaler="keda")
        assert tc.tofu_directory == Path("/data/run001/trial001/benchmark-infrastructure")

    def test_output_directory_is_trial_dir_joined_with_output(self) -> None:
        rc = self._make_run_context()
        tc = TrialContext(run_context=rc, trial_identifier="trial001", autoscaler="keda")
        assert tc.output_directory == Path("/data/run001/trial001/output")

    def test_preserves_run_context_reference(self) -> None:
        rc = self._make_run_context()
        tc = TrialContext(run_context=rc, trial_identifier="t1", autoscaler="hpa")
        assert tc.run_context is rc

    def test_preserves_trial_identifier(self) -> None:
        rc = self._make_run_context()
        tc = TrialContext(run_context=rc, trial_identifier="trial-xyz", autoscaler="vpa")
        assert tc.trial_identifier == "trial-xyz"

    def test_preserves_autoscaler(self) -> None:
        rc = self._make_run_context()
        tc = TrialContext(run_context=rc, trial_identifier="t1", autoscaler="karpenter")
        assert tc.autoscaler == "karpenter"


class TestTofuOutputs:
    """TofuOutputs stores parsed tofu output values."""

    def _make_tofu_outputs(self, **kwargs) -> TofuOutputs:
        defaults = {
            "benchmark_runner_public_ip": "10.0.1.5",
            "ssh_key_pair_name": "key-1",
            "control_plane_private_ip": "10.0.2.206",
            "amd_worker_private_ips": ["10.0.2.116"],
            "arm_worker_private_ips": ["10.0.2.4"],
            "globeco_dns": "kasb-xxx.elb.us-east-1.amazonaws.com",
            "globeco_port": 80,
            "execution_data_fs": "fs-01234567",
            "raw_json": {},
        }
        defaults.update(kwargs)
        return TofuOutputs(**defaults)

    def test_stores_benchmark_runner_public_ip(self) -> None:
        to = self._make_tofu_outputs(benchmark_runner_public_ip="10.0.1.5")
        assert to.benchmark_runner_public_ip == "10.0.1.5"

    def test_stores_ssh_key_pair_name(self) -> None:
        to = self._make_tofu_outputs(ssh_key_pair_name="kasbench-trial001")
        assert to.ssh_key_pair_name == "kasbench-trial001"

    def test_stores_raw_json(self) -> None:
        raw = {"benchmark_runner": {"value": {"public_ip": "1.2.3.4"}}}
        to = self._make_tofu_outputs(raw_json=raw)
        assert to.raw_json == raw

    def test_stores_control_plane_private_ip(self) -> None:
        to = self._make_tofu_outputs(control_plane_private_ip="10.0.2.206")
        assert to.control_plane_private_ip == "10.0.2.206"

    def test_stores_amd_worker_private_ips(self) -> None:
        to = self._make_tofu_outputs(amd_worker_private_ips=["10.0.2.116", "10.0.2.117"])
        assert to.amd_worker_private_ips == ["10.0.2.116", "10.0.2.117"]

    def test_stores_arm_worker_private_ips(self) -> None:
        to = self._make_tofu_outputs(arm_worker_private_ips=["10.0.2.4", "10.0.2.5"])
        assert to.arm_worker_private_ips == ["10.0.2.4", "10.0.2.5"]

    def test_stores_globeco_dns(self) -> None:
        to = self._make_tofu_outputs(globeco_dns="kasb-xxx.elb.us-east-1.amazonaws.com")
        assert to.globeco_dns == "kasb-xxx.elb.us-east-1.amazonaws.com"

    def test_stores_globeco_port(self) -> None:
        to = self._make_tofu_outputs(globeco_port=8080)
        assert to.globeco_port == 8080

    def test_none_values_for_optional_fields(self) -> None:
        to = self._make_tofu_outputs(
            benchmark_runner_public_ip=None,
            ssh_key_pair_name=None,
            control_plane_private_ip=None,
            globeco_dns=None,
            globeco_port=None,
        )
        assert to.benchmark_runner_public_ip is None
        assert to.ssh_key_pair_name is None
        assert to.control_plane_private_ip is None
        assert to.globeco_dns is None
        assert to.globeco_port is None


class TestTrialConfig:
    """TrialConfig stores persisted trial configuration."""

    def _make_trial_config(self, **kwargs) -> TrialConfig:
        defaults = {
            "aws_region": "us-east-1",
            "s3_bucket": "my-bucket",
            "run_duration": 30,
            "benchmark_runner_public_ip": "1.2.3.4",
            "ssh_key_pair_name": "kasbench-trial001",
            "control_plane_private_ip": "10.0.2.206",
            "amd_worker_private_ips": ["10.0.2.116"],
            "arm_worker_private_ips": ["10.0.2.4"],
            "globeco_dns": "kasb-xxx.elb.us-east-1.amazonaws.com",
            "globeco_port": 80,
            "execution_data_fs": "fs-01234567",
        }
        defaults.update(kwargs)
        return TrialConfig(**defaults)

    def test_stores_all_fields(self) -> None:
        tc = self._make_trial_config()
        assert tc.aws_region == "us-east-1"
        assert tc.s3_bucket == "my-bucket"
        assert tc.run_duration == 30
        assert tc.benchmark_runner_public_ip == "1.2.3.4"
        assert tc.ssh_key_pair_name == "kasbench-trial001"
        assert tc.control_plane_private_ip == "10.0.2.206"
        assert tc.amd_worker_private_ips == ["10.0.2.116"]
        assert tc.arm_worker_private_ips == ["10.0.2.4"]
        assert tc.globeco_dns == "kasb-xxx.elb.us-east-1.amazonaws.com"
        assert tc.globeco_port == 80

    def test_multiple_worker_ips(self) -> None:
        tc = self._make_trial_config(
            amd_worker_private_ips=["10.0.2.10", "10.0.2.11", "10.0.2.12"],
            arm_worker_private_ips=["10.0.3.1", "10.0.3.2"],
        )
        assert tc.amd_worker_private_ips == ["10.0.2.10", "10.0.2.11", "10.0.2.12"]
        assert tc.arm_worker_private_ips == ["10.0.3.1", "10.0.3.2"]


class TestLoadTrialConfig:
    """load_trial_config reads trial_config.json from the output directory."""

    def _make_trial_context(self, tmp_path: Path) -> TrialContext:
        rc = RunContext(working_directory=tmp_path, run_identifier="run001")
        return TrialContext(run_context=rc, trial_identifier="trial001", autoscaler="keda")

    def test_loads_valid_config(self, tmp_path: Path) -> None:
        trial_ctx = self._make_trial_context(tmp_path)
        trial_ctx.output_directory.mkdir(parents=True)
        config_data = {
            "aws_region": "eu-west-1",
            "s3_bucket": "test-bucket",
            "run_duration": 60,
            "benchmark_runner_public_ip": "5.6.7.8",
            "ssh_key_pair_name": "key-pair",
            "control_plane_private_ip": "10.0.1.1",
            "amd_worker_private_ips": ["10.0.1.2"],
            "arm_worker_private_ips": ["10.0.1.3"],
            "globeco_dns": "my-nlb.elb.amazonaws.com",
            "globeco_port": 443,
            "execution_data_fs": "fs-abcdef01",
        }
        (trial_ctx.output_directory / "trial_config.json").write_text(json.dumps(config_data))

        result = load_trial_config(trial_ctx)

        assert result.aws_region == "eu-west-1"
        assert result.s3_bucket == "test-bucket"
        assert result.run_duration == 60
        assert result.benchmark_runner_public_ip == "5.6.7.8"
        assert result.ssh_key_pair_name == "key-pair"
        assert result.control_plane_private_ip == "10.0.1.1"
        assert result.amd_worker_private_ips == ["10.0.1.2"]
        assert result.arm_worker_private_ips == ["10.0.1.3"]
        assert result.globeco_dns == "my-nlb.elb.amazonaws.com"
        assert result.globeco_port == 443

    def test_raises_kasbench_error_if_file_missing(self, tmp_path: Path) -> None:
        trial_ctx = self._make_trial_context(tmp_path)
        trial_ctx.output_directory.mkdir(parents=True)

        import pytest

        with pytest.raises(KasbenchError, match="Trial config not found"):
            load_trial_config(trial_ctx)

    def test_raises_kasbench_error_if_json_invalid(self, tmp_path: Path) -> None:
        trial_ctx = self._make_trial_context(tmp_path)
        trial_ctx.output_directory.mkdir(parents=True)
        (trial_ctx.output_directory / "trial_config.json").write_text("not json{{{")

        import pytest

        with pytest.raises(KasbenchError, match="Failed to read trial config"):
            load_trial_config(trial_ctx)


class TestSaveTrialConfig:
    """save_trial_config writes trial_config.json to the output directory."""

    def _make_trial_context(self, tmp_path: Path) -> TrialContext:
        rc = RunContext(working_directory=tmp_path, run_identifier="run001")
        return TrialContext(run_context=rc, trial_identifier="trial001", autoscaler="keda")

    def test_writes_config_file(self, tmp_path: Path) -> None:
        trial_ctx = self._make_trial_context(tmp_path)
        config = TrialConfig(
            aws_region="us-west-2",
            s3_bucket="bucket-xyz",
            run_duration=45,
            benchmark_runner_public_ip="9.8.7.6",
            ssh_key_pair_name="my-key",
            control_plane_private_ip="10.0.0.1",
            amd_worker_private_ips=["10.0.0.2", "10.0.0.3"],
            arm_worker_private_ips=["10.0.0.4"],
            globeco_dns="nlb.example.com",
            globeco_port=8080,
            execution_data_fs="fs-99999999",
        )

        save_trial_config(trial_ctx, config)

        config_path = trial_ctx.output_directory / "trial_config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert data["aws_region"] == "us-west-2"
        assert data["s3_bucket"] == "bucket-xyz"
        assert data["run_duration"] == 45
        assert data["benchmark_runner_public_ip"] == "9.8.7.6"
        assert data["ssh_key_pair_name"] == "my-key"
        assert data["control_plane_private_ip"] == "10.0.0.1"
        assert data["amd_worker_private_ips"] == ["10.0.0.2", "10.0.0.3"]
        assert data["arm_worker_private_ips"] == ["10.0.0.4"]
        assert data["globeco_dns"] == "nlb.example.com"
        assert data["globeco_port"] == 8080

    def test_creates_output_directory_if_not_exists(self, tmp_path: Path) -> None:
        trial_ctx = self._make_trial_context(tmp_path)
        config = TrialConfig(
            aws_region="us-east-1",
            s3_bucket="b",
            run_duration=10,
            benchmark_runner_public_ip="1.1.1.1",
            ssh_key_pair_name="k",
            control_plane_private_ip="10.0.0.1",
            amd_worker_private_ips=[],
            arm_worker_private_ips=[],
            globeco_dns="dns.example.com",
            globeco_port=80,
            execution_data_fs="fs-00000000",
        )

        save_trial_config(trial_ctx, config)

        assert (trial_ctx.output_directory / "trial_config.json").exists()

    def test_roundtrip_save_load(self, tmp_path: Path) -> None:
        trial_ctx = self._make_trial_context(tmp_path)
        config = TrialConfig(
            aws_region="ap-southeast-1",
            s3_bucket="roundtrip-bucket",
            run_duration=120,
            benchmark_runner_public_ip="3.4.5.6",
            ssh_key_pair_name="rt-key",
            control_plane_private_ip="172.16.0.1",
            amd_worker_private_ips=["172.16.0.2", "172.16.0.3"],
            arm_worker_private_ips=["172.16.0.4", "172.16.0.5", "172.16.0.6"],
            globeco_dns="rt-dns.example.com",
            globeco_port=9090,
            execution_data_fs="fs-roundtrip",
        )

        save_trial_config(trial_ctx, config)
        loaded = load_trial_config(trial_ctx)

        assert loaded.aws_region == config.aws_region
        assert loaded.s3_bucket == config.s3_bucket
        assert loaded.run_duration == config.run_duration
        assert loaded.benchmark_runner_public_ip == config.benchmark_runner_public_ip
        assert loaded.ssh_key_pair_name == config.ssh_key_pair_name
        assert loaded.control_plane_private_ip == config.control_plane_private_ip
        assert loaded.amd_worker_private_ips == config.amd_worker_private_ips
        assert loaded.arm_worker_private_ips == config.arm_worker_private_ips
        assert loaded.globeco_dns == config.globeco_dns
        assert loaded.globeco_port == config.globeco_port
