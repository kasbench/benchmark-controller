# KASBench Controller

A Python CLI application that orchestrates Kubernetes autoscaling benchmark execution. The Controller runs on a Bastion Host and manages benchmark trials by provisioning AWS infrastructure via Open Tofu, tracking trial state in a local SQLite database, and emitting structured JSON Lines logs for full auditability.

## Architecture

The Controller operates from a Bastion Host that has VPC peering access to the benchmark VPC. Each benchmark trial provisions:

- A **Benchmark Runner** (EC2 instance with public IP) that executes workloads
- A **Control Plane** node running Kubernetes
- **Worker Node Groups** (amd64 and arm64) that host the autoscaled workloads
- Supporting infrastructure (VPC, subnets, NLB, NAT gateway)

```
Bastion Host (Controller)
    │
    ├── kasbench init          → Creates run directory + SQLite database
    └── kasbench build-infrastructure  → Provisions trial infrastructure via Open Tofu
```

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Open Tofu](https://opentofu.org/) (`tofu` binary on PATH)
- AWS credentials configured for the target account
- Network access to GitHub (for downloading infrastructure code)

## Installation

```bash
# Clone the repository
git clone https://github.com/kasbench/kasbench-controller.git
cd kasbench-controller

# Install dependencies
uv sync

# Verify installation
uv run kasbench --help
```

## Usage

### Global Options

All subcommands support these global options:

| Option | Description |
|--------|-------------|
| `--log <path>` | Write structured JSON Lines logs to a file (in addition to stdout) |
| `--dry-run` | Report all planned operations without executing them |

### Initialize a Run

Create a new experimental run with a working directory and SQLite database:

```bash
kasbench init \
  --working-directory /data/benchmarks \
  --run-identifier run001
```

This creates:
```
/data/benchmarks/
└── run001/
    └── benchmark.db    # SQLite database with trials and events tables
```

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Unique name for this experimental run |
| `--force` | No | Delete and recreate the run directory if it already exists |

### Build Infrastructure

Provision AWS infrastructure for a single benchmark trial:

```bash
kasbench build-infrastructure \
  --working-directory /data/benchmarks \
  --run-identifier run001 \
  --trial-identifier trial001 \
  --autoscaler karpenter \
  --var-file production.tfvars \
  --var "instance_type=c8i.4xlarge" \
  --auto-approve
```

This performs the following steps:
1. Validates the run directory and database exist
2. Creates the trial directory and output subdirectory
3. Downloads the benchmark-infrastructure repository from GitHub
4. Inserts a trial record in the database (status: PENDING)
5. Runs `tofu init` to initialize the Open Tofu workspace
6. Runs `tofu apply` to provision infrastructure
7. Captures outputs (benchmark runner IP, SSH key pair name)
8. Updates the trial record (status: INIT)

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Name of the experimental run (must be initialized) |
| `--trial-identifier` | Yes | Unique name for this trial within the run |
| `--autoscaler` | Yes | Autoscaler being benchmarked (e.g., karpenter, keda, hpa) |
| `--var-file` | No | Tofu var-file (repeatable). Filenames without path separators resolve to `environments/` |
| `--var` | No | Tofu variable assignment as `key=value` (repeatable) |
| `--auto-approve` | No | Skip interactive plan approval |
| `--force` | No | Delete and recreate the trial directory if it already exists |
| `--no-apply` | No | Stop after `tofu init` without applying |

### Dry-Run Mode

Preview what any command would do without making changes:

```bash
kasbench --dry-run init \
  --working-directory /data/benchmarks \
  --run-identifier run001
```

### Logging

All operations emit structured JSON Lines to stdout:

```json
{"timestamp": "2026-06-01T10:30:00Z", "level": "info", "step": "create_run_directory", "outcome": "success", "path": "/data/benchmarks/run001"}
{"timestamp": "2026-06-01T10:30:01Z", "level": "info", "step": "create_database", "outcome": "success", "path": "/data/benchmarks/run001/benchmark.db"}
```

Write logs to a file for later analysis:

```bash
kasbench --log /var/log/kasbench.jsonl build-infrastructure ...
```

## Directory Structure

After running `init` and `build-infrastructure`, the working directory looks like:

```
/data/benchmarks/
└── run001/
    ├── benchmark.db
    └── trial001/
        ├── benchmark-infrastructure/    # Open Tofu HCL files and state
        │   ├── main.tf
        │   ├── variables.tf
        │   ├── environments/
        │   └── .terraform/
        └── output/
            └── tofu_outputs.json        # Captured infrastructure outputs
```

## Database Schema

The SQLite database (`benchmark.db`) tracks trial lifecycle:

**trials** — One row per benchmark trial:
- `trial_id`, `status` (PENDING → INIT → RUNNING → SUCCESS/FAIL)
- `run_identifier`, `trial_identifier`, `autoscaler`
- `benchmark_runner_public_ip`, `ssh_key_pair_name`
- Timestamps: `record_created_time`, `infra_start_time`, `infra_end_time`, `benchmark_start_time`, `benchmark_end_time`

**events** — Audit log of trial events:
- `event_id`, `trial_id`, `event_time`, `event_type`, `event_message`

## Development

```bash
# Install dev dependencies
uv sync

# Run tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_database.py
```

### Project Layout

```
src/kasbench_controller/
├── __init__.py
├── cli.py                  # Click CLI group and global options
├── commands/
│   ├── init.py             # init subcommand
│   └── build_infrastructure.py  # build-infrastructure subcommand
├── database.py             # SQLite schema and operations
├── tofu.py                 # Open Tofu subprocess wrapper
├── repository.py           # GitHub repository download
├── output_parser.py        # Tofu JSON output parsing
├── logging.py              # Structured logging (structlog)
├── models.py               # Domain dataclasses
└── exceptions.py           # Custom exception hierarchy
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (structured log entry emitted with details) |

## License

This project is part of a dissertation research effort on Kubernetes autoscaling benchmarking.
