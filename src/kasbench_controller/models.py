"""Domain data classes for the KASBench Controller."""

from dataclasses import dataclass, field
from pathlib import Path


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
    raw_json: dict
