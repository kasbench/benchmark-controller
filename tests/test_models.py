"""Tests for the kasbench_controller.models module."""

from pathlib import Path

from kasbench_controller.models import RunContext, TrialContext, TofuOutputs


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

    def test_stores_benchmark_runner_public_ip(self) -> None:
        to = TofuOutputs(
            benchmark_runner_public_ip="10.0.1.5",
            ssh_key_pair_name="key-1",
            raw_json={},
        )
        assert to.benchmark_runner_public_ip == "10.0.1.5"

    def test_stores_ssh_key_pair_name(self) -> None:
        to = TofuOutputs(
            benchmark_runner_public_ip="1.2.3.4",
            ssh_key_pair_name="kasbench-trial001",
            raw_json={},
        )
        assert to.ssh_key_pair_name == "kasbench-trial001"

    def test_stores_raw_json(self) -> None:
        raw = {"benchmark_runner": {"value": {"public_ip": "1.2.3.4"}}}
        to = TofuOutputs(
            benchmark_runner_public_ip="1.2.3.4",
            ssh_key_pair_name="key",
            raw_json=raw,
        )
        assert to.raw_json == raw
