"""Domain data classes for the KASBench Controller."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from kasbench_controller.exceptions import KasbenchError


TRIAL_CONFIG_FILENAME = "trial_config.json"


@dataclass
class RunContext:
    """Context for a benchmark run, shared across commands."""

    working_directory: Path
    run_identifier: str
    run_directory: Path = field(init=False)
    db_path: Path = field(init=False)

    def __post_init__(self):
        self.run_directory = self.working_directory / self.run_identifier
        self.db_path = self.run_directory / "benchmark.db"


@dataclass
class TrialContext:
    """Context for a single trial within a run."""

    run_context: RunContext
    trial_identifier: str
    autoscaler: str
    trial_directory: Path = field(init=False)
    tofu_directory: Path = field(init=False)
    output_directory: Path = field(init=False)

    def __post_init__(self):
        self.trial_directory = self.run_context.run_directory / self.trial_identifier
        self.tofu_directory = self.trial_directory / "benchmark-infrastructure"
        self.output_directory = self.trial_directory / "output"


@dataclass
class TofuOutputs:
    """Parsed outputs from tofu output -json."""

    benchmark_runner_public_ip: str | None
    ssh_key_pair_name: str | None
    control_plane_private_ip: str | None
    amd_worker_private_ips: list[str]
    arm_worker_private_ips: list[str]
    globeco_dns: str | None
    globeco_port: int | None
    execution_data_fs: str | None
    raw_json: dict


@dataclass
class TrialConfig:
    """Persisted trial configuration loaded by subsequent commands."""

    aws_region: str
    s3_bucket: str
    run_duration: int
    benchmark_runner_public_ip: str
    ssh_key_pair_name: str
    control_plane_private_ip: str
    amd_worker_private_ips: list[str]
    arm_worker_private_ips: list[str]
    globeco_dns: str
    globeco_port: int
    execution_data_fs: str


def load_trial_config(trial_ctx: TrialContext) -> TrialConfig:
    """Load trial configuration from trial_config.json in the trial output directory.

    Raises KasbenchError if the file does not exist or cannot be parsed.
    """
    config_path = trial_ctx.output_directory / TRIAL_CONFIG_FILENAME
    if not config_path.exists():
        raise KasbenchError(
            f"Trial config not found at {config_path}. "
            f"Has build-infrastructure been run for this trial?"
        )
    try:
        data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise KasbenchError(
            f"Failed to read trial config at {config_path}: {exc}"
        ) from exc

    return TrialConfig(
        aws_region=data["aws_region"],
        s3_bucket=data["s3_bucket"],
        run_duration=data["run_duration"],
        benchmark_runner_public_ip=data["benchmark_runner_public_ip"],
        ssh_key_pair_name=data["ssh_key_pair_name"],
        control_plane_private_ip=data["control_plane_private_ip"],
        amd_worker_private_ips=data["amd_worker_private_ips"],
        arm_worker_private_ips=data["arm_worker_private_ips"],
        globeco_dns=data["globeco_dns"],
        globeco_port=data["globeco_port"],
        execution_data_fs=data["execution_data_fs"],
    )


def save_trial_config(trial_ctx: TrialContext, config: TrialConfig) -> None:
    """Write trial configuration to trial_config.json in the trial output directory."""
    config_path = trial_ctx.output_directory / TRIAL_CONFIG_FILENAME
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "aws_region": config.aws_region,
        "s3_bucket": config.s3_bucket,
        "run_duration": config.run_duration,
        "benchmark_runner_public_ip": config.benchmark_runner_public_ip,
        "ssh_key_pair_name": config.ssh_key_pair_name,
        "control_plane_private_ip": config.control_plane_private_ip,
        "amd_worker_private_ips": config.amd_worker_private_ips,
        "arm_worker_private_ips": config.arm_worker_private_ips,
        "globeco_dns": config.globeco_dns,
        "globeco_port": config.globeco_port,
        "execution_data_fs": config.execution_data_fs,
    }
    config_path.write_text(json.dumps(data, indent=2))
