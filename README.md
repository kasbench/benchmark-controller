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
    ├── kasbench init                    → Creates run directory + SQLite database
    ├── kasbench build-infrastructure    → Provisions trial infrastructure via Open Tofu
    ├── kasbench initialize-runner       → Pulls runner image, starts container, initializes benchmark
    ├── kasbench benchmark-start         → Triggers load generation
    ├── kasbench benchmark-monitor       → Polls status until benchmark completes
    ├── kasbench benchmark-postprocessing → Exports artifacts to S3
    ├── kasbench shutdown                → Shuts down the Runner API
    ├── kasbench destroy-infrastructure  → Tears down all AWS resources
    └── kasbench run-experiment          → Orchestrates multi-trial experiments end-to-end
```

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Open Tofu](https://opentofu.org/) (`tofu` binary on PATH)
- [AWS CLI](https://aws.amazon.com/cli/) (`aws` binary on PATH, used for S3 uploads)
- AWS credentials configured for the target account
- Network access to GitHub (for downloading infrastructure code)
- SSH access to the Benchmark Runner host (for `initialize-runner`)

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
  --aws-region us-east-1 \
  --s3-bucket my-kasbench-bucket \
  --run-duration 30 \
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
7. Captures outputs (benchmark runner IP, SSH key pair name, cluster topology)
8. Uploads artifacts to S3 (tofu outputs, environment descriptions)
9. Writes `trial_config.json` for use by subsequent commands
10. Updates the trial record (status: INIT)

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Name of the experimental run (must be initialized) |
| `--trial-identifier` | Yes | Unique name for this trial within the run |
| `--autoscaler` | Yes | Autoscaler being benchmarked (e.g., karpenter, keda, hpa) |
| `--aws-region` | No | AWS region for infrastructure deployment (default: `us-east-1`) |
| `--s3-bucket` | Yes | S3 bucket for artifact storage |
| `--run-duration` | Yes | Benchmark run duration in minutes |
| `--var-file` | No | Tofu var-file (repeatable). Filenames without path separators resolve to `environments/` |
| `--var` | No | Tofu variable assignment as `key=value` (repeatable) |
| `--auto-approve` | No | Skip interactive plan approval |
| `--force` | No | Delete and recreate the trial directory if it already exists |
| `--no-apply` | No | Stop after `tofu init` without applying |

### Initialize Runner

Set up the KASBench Runner container on the benchmark host and initialize it with the cluster topology:

```bash
kasbench initialize-runner \
  --working-directory /data/benchmarks \
  --run-identifier run001 \
  --trial-identifier trial001 \
  --runner-version 0.2.0 \
  --health-timeout 30 \
  --rollout-timeout 600
```

This performs the following steps:
1. Loads `trial_config.json` (verifies `build-infrastructure` completed)
2. Pulls the KASBench Runner Docker image via SSH
3. Creates the `kasbench` Docker network on the runner host
4. Starts the `kasbench-runner` container with port 8080 exposed
5. Polls the runner health endpoint until it responds with HTTP 200
6. Sends the initialization request with cluster topology and benchmark parameters
7. Waits for the Kubernetes deployment rollout to complete
8. Takes a pre-benchmark snapshot

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Name of the experimental run |
| `--trial-identifier` | Yes | Name of the trial within the run |
| `--runner-version` | No | KASBench Runner Docker image version (default: `0.2.0`) |
| `--health-timeout` | No | Health check polling timeout in seconds (default: `30`) |
| `--rollout-timeout` | No | Rollout wait timeout in seconds (default: `600`) |
| `--cluster-cidr-range` | No | Pod network CIDR to pass to the Runner (e.g. `10.244.0.0/16`). If omitted, the Runner uses its default. |

### Benchmark Start

Trigger load generation against the cluster:

```bash
kasbench benchmark-start \
  --working-directory /data/benchmarks \
  --run-identifier run001 \
  --trial-identifier trial001
```

Sends a `POST /start` request to the Runner API to begin the benchmark. Records the benchmark start time in the database.

You can optionally override the benchmark duration and per-role load generation parameters:

```bash
kasbench benchmark-start \
  --working-directory /data/benchmarks \
  --run-identifier run001 \
  --trial-identifier trial001 \
  --benchmark-length-minutes 15 \
  --role-params '{"trader":{"baseLoadIntensity":50,"baseDelayPercentage":60,"spawnRate":5},"investor":{"baseLoadIntensity":20,"baseDelayPercentage":80,"spawnRate":2}}'
```

When `--benchmark-length-minutes` is omitted, the Runner uses the `runDurationMinutes` value from `/initialize`. When `--role-params` is omitted (or a role is not included in the JSON), the Runner applies its built-in defaults:

| Role | baseLoadIntensity | baseDelayPercentage | spawnRate |
|------|-------------------|---------------------|-----------|
| `back-office` | 100 | 80 | 10 |
| `portfolio-manager` | 100 | 80 | 10 |
| `trader` | 100 | 80 | 10 |
| `investor` | 10 | 80 | 10 |
| `it-operations` | 100 | 100 | 1 |

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Name of the experimental run |
| `--trial-identifier` | Yes | Name of the trial within the run |
| `--benchmark-length-minutes` | No | Override benchmark duration in minutes (>=1). Defaults to the value from `/initialize`. |
| `--role-params` | No | Per-role load generation overrides as a JSON string. Each role value must include `baseLoadIntensity`, `baseDelayPercentage`, and `spawnRate`. |

### Benchmark Monitor

Poll the Runner API until the benchmark completes or a timeout is reached:

```bash
kasbench benchmark-monitor \
  --working-directory /data/benchmarks \
  --run-identifier run001 \
  --trial-identifier trial001 \
  --timeout 45 \
  --interval 30 \
  --verbose
```

Polls `GET /status` on the Runner API at the configured interval. Exits when the benchmark status transitions to `success` or `failed`, or when the timeout is reached.

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Name of the experimental run |
| `--trial-identifier` | Yes | Name of the trial within the run |
| `--timeout` | Yes | Maximum monitoring time in minutes |
| `--interval` | No | Status check interval in seconds (default: `30`) |
| `--verbose` | No | Print status messages to the console at each poll interval |

### Benchmark Postprocessing

Export all benchmark artifacts via the Runner API:

```bash
kasbench benchmark-postprocessing \
  --working-directory /data/benchmarks \
  --run-identifier run001 \
  --trial-identifier trial001
```

This performs the following steps:
1. Takes a post-benchmark snapshot
2. Exports metrics (`/metrics/export`)
3. Exports metadata (`/metadata/export`)
4. Exports TSDB data (`/tsdb/export`)
5. Exports output files (`/output/export`)
6. Exports the runner database (`/db/export`)

If any export step fails, the command exits with an error identifying the failed step.

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Name of the experimental run |
| `--trial-identifier` | Yes | Name of the trial within the run |

### Shutdown

Send a shutdown request to the Runner API:

```bash
kasbench shutdown \
  --working-directory /data/benchmarks \
  --run-identifier run001 \
  --trial-identifier trial001
```

Sends a `POST /shutdown` request to the Runner API to gracefully shut down the benchmark runner container.

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Name of the experimental run |
| `--trial-identifier` | Yes | Name of the trial within the run |

### Destroy Infrastructure

Tear down all AWS resources for a benchmark trial:

```bash
kasbench destroy-infrastructure \
  --working-directory /data/benchmarks \
  --run-identifier run001 \
  --trial-identifier trial001 \
  --auto-approve \
  --var-file production.tfvars \
  --ebs-wait 300
```

This performs the following steps:
1. Shuts down the Runner API container
2. Waits for EBS volumes to detach (prints progress every 30 seconds)
3. Runs `tofu destroy` to remove all AWS resources (unless `--no-apply` is set)
4. Records cleanup timestamps in the database

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--working-directory` | Yes | Top-level directory for all benchmark data |
| `--run-identifier` | Yes | Name of the experimental run |
| `--trial-identifier` | Yes | Name of the trial within the run |
| `--auto-approve` | No | Skip interactive approval for tofu destroy |
| `--var-file` | No | Var-file arguments for tofu destroy (repeatable) |
| `--var` | No | Variable assignment as `key=value` for tofu destroy (repeatable) |
| `--no-apply` | No | Skip the tofu destroy step entirely |
| `--ebs-wait` | No | Seconds to wait for EBS volume detachment (default: `300`) |

### Run Experiment

Orchestrate a complete multi-trial benchmark experiment from a single command. `run-experiment` automates the execution of multiple trials across a randomized schedule of autoscaler assignments, handling progress persistence, error recovery, and infrastructure cleanup automatically.

Use this command when you need to run many trials (e.g., 5 trials × 4 autoscalers = 20 trials) without manual intervention. Each trial progresses through the full benchmark lifecycle, and progress is persisted to S3 so experiments can resume from where they left off after transient failures.

```bash
kasbench run-experiment \
  --run-identifier exp-2024-001 \
  --autoscalers hpa,vpa,keda,none \
  --trials-per-autoscaler 5 \
  --run-duration 30 \
  --working-directory /data/benchmarks \
  --s3-bucket kasbench-results \
  --trial-prefix trial \
  --var-file us-east-1.tfvars \
  --auto-approve
```

**Trial Execution Flow:**

Each trial executes the following 10 steps in order:

1. **init** — Create the run directory and SQLite database (equivalent to `kasbench init`)
2. **build-infrastructure** — Provision AWS resources via Open Tofu
3. **wait** — Pause for 300 seconds (5 minutes) to allow infrastructure stabilization
4. **initialize-runner** — Pull the Runner Docker image, start the container, and initialize the benchmark
5. **benchmark-start** — Trigger load generation against the cluster
6. **benchmark-monitor** — Poll the Runner API until the benchmark completes (timeout = `--run-duration` + 5 minutes)
7. **benchmark-postprocessing** — Export metrics, metadata, TSDB data, output files, and the runner database
8. **shutdown** — Gracefully shut down the Runner API container
9. **destroy-infrastructure** — Tear down all AWS resources for the trial
10. **upload-logs** — Upload the trial output directory to S3 at `{s3-bucket}/{run-identifier}/{trial-identifier}/benchmark-results`

**Randomized Scheduling:**

Autoscaler assignments are randomized across all trials to mitigate temporal ordering effects. For example, with `--autoscalers hpa,vpa --trials-per-autoscaler 3`, the 6 trials will each be assigned an autoscaler (3× hpa, 3× vpa) in a random order. Use `--random-seed` to make the schedule deterministic and reproducible.

**Progress Persistence and Resumption:**

Progress is stored to S3 at `{s3-bucket}/{run-identifier}/experiment-progress.json` after each step completes. If the experiment is interrupted, rerunning the same command resumes from the first incomplete trial. Use `--rerun-from-failed` to resume from the specific failed step within a trial rather than restarting the trial from the beginning.

**Error Handling:**

When a trial step fails, the orchestrator initiates a two-tier abort sequence to clean up infrastructure:
1. Attempt `destroy-infrastructure` with the configured parameters
2. If that fails, attempt a direct `tofu destroy` from the trial's infrastructure directory

If both tiers fail, the experiment halts to prevent resource leakage. By default, the experiment continues to the next trial after a successful abort. Use `--halt-on-error` to stop the entire experiment at the first trial failure.

**Options:**

| Option | Required | Description |
|--------|----------|-------------|
| `--run-identifier` | Yes | Unique identifier for this experiment (1-128 chars, alphanumeric/hyphens/underscores) |
| `--autoscalers` | Yes | Comma-separated list of autoscalers to benchmark (`hpa`, `vpa`, `keda`, `none`) |
| `--trials-per-autoscaler` | Yes | Number of trials per autoscaler entry (1-9999) |
| `--run-duration` | Yes | Benchmark duration in minutes (minimum 1) |
| `--working-directory` | Yes | Top-level working directory for experiment artifacts |
| `--s3-bucket` | Yes | S3 bucket for progress persistence and artifact storage |
| `--trial-prefix` | No | Prefix for trial identifiers (default: `trial`) |
| `--aws-region` | No | AWS region (default: `us-east-1`) |
| `--var-file` | No | Var-file arguments for tofu (repeatable). Filenames without path separators resolve to `environments/` |
| `--var` | No | Variable assignment as `key=value` (repeatable) |
| `--auto-approve` | No | Skip interactive approval for infrastructure operations (default: `false`) |
| `--runner-version` | No | Runner version to deploy (default: `0.2.6`) |
| `--health-timeout` | No | Health check timeout in seconds (default: `30`) |
| `--rollout-timeout` | No | Rollout timeout in seconds (default: `600`) |
| `--cluster-cidr-range` | No | Cluster CIDR range for Flannel networking (default: `10.244.0.0/16`) |
| `--role-params` | No | Per-role load generation overrides as a JSON string. Each role must include `baseLoadIntensity`, `baseDelayPercentage`, and `spawnRate` |
| `--random-seed` | No | Seed for trial schedule randomization. Deterministic if provided; otherwise uses a non-deterministic source |
| `--ebs-wait` | No | EBS volume wait time in seconds (default: `300`) |
| `--rerun-from-failed` | No | Resume from the first failed step within a trial instead of restarting the trial from the beginning (default: `false`) |
| `--halt-on-error` | No | Stop the experiment at the first trial failure instead of continuing to the next trial (default: `false`) |

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
        │   ├── artifacts/trial001/      # Generated environment descriptions
        │   └── .terraform/
        └── output/
            ├── tofu_outputs.json        # Captured infrastructure outputs
            └── trial_config.json        # Trial configuration for subsequent commands
```

## Database Schema

The SQLite database (`benchmark.db`) tracks trial lifecycle:

**trials** — One row per benchmark trial:
- `trial_id`, `status` (PENDING → INIT → RUNNING → SUCCESS/FAIL)
- `run_identifier`, `trial_identifier`, `autoscaler`
- `benchmark_runner_public_ip`, `ssh_key_pair_name`
- Timestamps: `record_created_time`, `infra_start_time`, `infra_end_time`, `benchmark_start_time`, `benchmark_end_time`, `cleanup_start_time`, `cleanup_end_time`

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
│   ├── build_infrastructure.py  # build-infrastructure subcommand
│   ├── initialize_runner.py     # initialize-runner subcommand
│   ├── benchmark_start.py       # benchmark-start subcommand
│   ├── benchmark_monitor.py     # benchmark-monitor subcommand
│   ├── benchmark_postprocessing.py  # benchmark-postprocessing subcommand
│   ├── shutdown.py              # shutdown subcommand
│   ├── destroy_infrastructure.py    # destroy-infrastructure subcommand
│   └── run_experiment.py        # run-experiment subcommand
├── services/
│   ├── __init__.py
│   ├── init_service.py              # Extracted init logic
│   ├── build_infrastructure_service.py  # Extracted build-infrastructure logic
│   ├── initialize_runner_service.py     # Extracted initialize-runner logic
│   ├── benchmark_start_service.py       # Extracted benchmark-start logic
│   ├── benchmark_monitor_service.py     # Extracted benchmark-monitor logic
│   ├── benchmark_postprocessing_service.py  # Extracted benchmark-postprocessing logic
│   ├── shutdown_service.py              # Extracted shutdown logic
│   └── destroy_infrastructure_service.py    # Extracted destroy-infrastructure logic
├── experiment/
│   ├── __init__.py
│   ├── config.py           # ExperimentConfig dataclass
│   ├── models.py           # TrialAssignment, TrialResult, AbortResult, ExperimentProgress
│   ├── scheduler.py        # TrialScheduler (randomized schedule generation)
│   ├── progress.py         # ProgressManager (S3 persistence and resumption)
│   ├── abort.py            # AbortSequence (two-tier infrastructure cleanup)
│   ├── pipeline.py         # TrialPipeline (single-trial step execution)
│   ├── orchestrator.py     # ExperimentOrchestrator (top-level experiment runner)
│   └── experiment_logger.py # Structured experiment logging
├── database.py             # SQLite schema and operations
├── tofu.py                 # Open Tofu subprocess wrapper
├── repository.py           # GitHub repository download
├── output_parser.py        # Tofu JSON output parsing
├── logging.py              # Structured logging (structlog)
├── models.py               # Domain dataclasses
├── exceptions.py           # Custom exception hierarchy
├── s3_uploader.py          # S3 artifact upload via AWS CLI
├── ssh_executor.py         # Remote command execution via SSH
└── runner_api.py           # Runner API HTTP client
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (structured log entry emitted with details) |

## License

This project is part of a dissertation research effort on Kubernetes autoscaling benchmarking.
