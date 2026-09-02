"""Run-experiment subcommand - orchestrates multi-trial benchmark experiments."""

import json
import re
import sys
from pathlib import Path

import click

from kasbench_controller.experiment.config import ExperimentConfig
from kasbench_controller.experiment.orchestrator import ExperimentOrchestrator
from kasbench_controller.logging import configure_logging


# --- Validation callbacks ---

_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

VALID_AUTOSCALERS = {"hpa", "vpa", "keda", "none"}


def validate_run_identifier(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Validate run-identifier: 1-128 chars, alphanumeric/hyphens/underscores."""
    if not value or len(value) > 128:
        raise click.BadParameter(
            f"--run-identifier must be between 1 and 128 characters, got {len(value)}."
        )
    if not _IDENTIFIER_PATTERN.match(value):
        raise click.BadParameter(
            "--run-identifier must contain only alphanumeric characters, hyphens, and underscores."
        )
    return value


def validate_trial_prefix(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Validate trial-prefix: 1-64 chars, alphanumeric/hyphens/underscores."""
    if not value or len(value) > 64:
        raise click.BadParameter(
            f"--trial-prefix must be between 1 and 64 characters, got {len(value)}."
        )
    if not _IDENTIFIER_PATTERN.match(value):
        raise click.BadParameter(
            "--trial-prefix must contain only alphanumeric characters, hyphens, and underscores."
        )
    return value


def validate_autoscalers(ctx: click.Context, param: click.Parameter, value: str) -> list[str]:
    """Validate autoscalers: comma-separated, each in {"hpa","vpa","keda","none"}, at least one."""
    entries = [entry.strip() for entry in value.split(",")]
    entries = [entry for entry in entries if entry]  # Remove empty entries from trailing commas

    if not entries:
        raise click.BadParameter(
            "--autoscalers must contain at least one value from: hpa, vpa, keda, none."
        )

    for entry in entries:
        if entry not in VALID_AUTOSCALERS:
            raise click.BadParameter(
                f"Invalid autoscaler value '{entry}'. "
                f"Must be one of: hpa, vpa, keda, none."
            )

    return entries


def validate_var(ctx: click.Context, param: click.Parameter, value: tuple[str, ...]) -> tuple[str, ...]:
    """Validate --var entries: each must contain = with non-empty key."""
    for var in value:
        if "=" not in var:
            raise click.BadParameter(
                f"Malformed variable assignment '{var}'. Expected format: key=value."
            )
        key, _, _ = var.partition("=")
        if not key:
            raise click.BadParameter(
                f"Malformed variable assignment '{var}'. Key must not be empty."
            )
    return value


def validate_role_params(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> dict | None:
    """Validate role-params: parse JSON, validate structure."""
    if value is None:
        return None

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for --role-params: {e}")

    if not isinstance(parsed, dict):
        raise click.BadParameter("--role-params must be a JSON object.")

    required_keys = {"baseLoadIntensity", "baseDelayPercentage", "spawnRate"}
    for role_name, params in parsed.items():
        if not isinstance(params, dict):
            raise click.BadParameter(
                f"--role-params: value for role '{role_name}' must be an object."
            )
        missing = required_keys - set(params.keys())
        if missing:
            raise click.BadParameter(
                f"--role-params: role '{role_name}' is missing required keys: "
                f"{sorted(missing)}."
            )

    return parsed


# --- Click command ---


@click.command("run-experiment")
@click.option(
    "--run-identifier",
    required=True,
    type=str,
    callback=validate_run_identifier,
    is_eager=True,
    help="Unique identifier for this experiment (1-128 chars, alphanumeric/hyphens/underscores).",
)
@click.option(
    "--trial-prefix",
    required=False,
    type=str,
    default="trial",
    callback=validate_trial_prefix,
    help="Prefix for trial identifiers (default: 'trial').",
)
@click.option(
    "--autoscalers",
    required=True,
    type=str,
    callback=validate_autoscalers,
    help="Comma-separated list of autoscalers to benchmark (hpa, vpa, keda, none).",
)
@click.option(
    "--trials-per-autoscaler",
    required=True,
    type=click.IntRange(min=1, max=9999),
    help="Number of trials per autoscaler entry (1-9999).",
)
@click.option(
    "--run-duration",
    required=True,
    type=click.IntRange(min=1),
    help="Benchmark duration in minutes (minimum 1).",
)
@click.option(
    "--working-directory",
    required=True,
    type=click.Path(),
    help="Top-level working directory for experiment artifacts.",
)
@click.option(
    "--s3-bucket",
    required=True,
    type=str,
    help="S3 bucket for progress persistence and artifact storage.",
)
@click.option(
    "--aws-region",
    required=False,
    type=str,
    default="us-east-1",
    help="AWS region (default: us-east-1).",
)
@click.option(
    "--var-file",
    multiple=True,
    type=str,
    help="Var-file arguments for tofu (repeatable). Filenames without path separators resolve to environments/ directory.",
)
@click.option(
    "--var",
    "variables",
    multiple=True,
    type=str,
    callback=validate_var,
    help="Variable assignments in key=value format (repeatable).",
)
@click.option(
    "--auto-approve",
    is_flag=True,
    default=False,
    help="Skip interactive approval for infrastructure operations.",
)
@click.option(
    "--runner-version",
    required=False,
    type=str,
    default="0.2.6",
    help="Runner version to deploy (default: 0.2.6).",
)
@click.option(
    "--health-timeout",
    required=False,
    type=click.IntRange(min=1),
    default=30,
    help="Health check timeout in seconds (default: 30).",
)
@click.option(
    "--rollout-timeout",
    required=False,
    type=click.IntRange(min=1),
    default=600,
    help="Rollout timeout in seconds (default: 600).",
)
@click.option(
    "--cluster-cidr-range",
    required=False,
    type=str,
    default="10.244.0.0/16",
    help="Cluster CIDR range for Flannel networking (default: 10.244.0.0/16).",
)
@click.option(
    "--role-params",
    required=False,
    type=str,
    default=None,
    callback=validate_role_params,
    help="Per-role load generation overrides as a JSON string.",
)
@click.option(
    "--random-seed",
    required=False,
    type=int,
    default=None,
    help="Seed for trial schedule randomization (deterministic if provided).",
)
@click.option(
    "--ebs-wait",
    required=False,
    type=click.IntRange(min=1),
    default=300,
    help="EBS volume wait time in seconds (default: 300).",
)
@click.option(
    "--rerun-from-failed",
    is_flag=True,
    default=False,
    help="Resume from the first failed step rather than the first incomplete trial.",
)
@click.option(
    "--halt-on-error",
    is_flag=True,
    default=False,
    help="Stop the experiment at the first trial failure instead of continuing.",
)
@click.option(
    "--max-trial-retries",
    required=False,
    type=click.IntRange(min=0),
    default=3,
    help=(
        "Maximum consecutive rerun attempts for a failed (non-spot) trial slot "
        "before halting the experiment. Failed trials are rerun so each autoscaler "
        "reaches its target successful-trial count. Set to 0 to retry indefinitely "
        "(default: 3). Ignored when --halt-on-error is set."
    ),
)
@click.pass_context
def run_experiment_cmd(
    ctx: click.Context,
    run_identifier: str,
    trial_prefix: str,
    autoscalers: list[str],
    trials_per_autoscaler: int,
    run_duration: int,
    working_directory: str,
    s3_bucket: str,
    aws_region: str,
    var_file: tuple[str, ...],
    variables: tuple[str, ...],
    auto_approve: bool,
    runner_version: str,
    health_timeout: int,
    rollout_timeout: int,
    cluster_cidr_range: str,
    role_params: dict | None,
    random_seed: int | None,
    ebs_wait: int,
    rerun_from_failed: bool,
    halt_on_error: bool,
    max_trial_retries: int,
) -> None:
    """Orchestrate a multi-trial benchmark experiment.

    Runs multiple trials across randomized autoscaler assignments, with progress
    persistence to S3 and automatic error recovery. Each trial progresses through
    the full benchmark lifecycle: init, build-infrastructure, wait, initialize-runner,
    benchmark-start, benchmark-monitor, benchmark-postprocessing, shutdown,
    destroy-infrastructure, and upload-logs.
    """
    logger = ctx.obj.get("logger") or configure_logging(
        log_file=ctx.obj.get("log_file"), dry_run=ctx.obj.get("dry_run", False)
    )

    # Construct ExperimentConfig from validated parameters
    # Derive spot_enabled from --var spot=... (defaults to True if not specified)
    spot_enabled = True
    for var in variables:
        key, _, val = var.partition("=")
        if key == "spot" and val.lower() == "false":
            spot_enabled = False
            break

    config = ExperimentConfig(
        run_identifier=run_identifier,
        trial_prefix=trial_prefix,
        autoscalers=autoscalers,
        trials_per_autoscaler=trials_per_autoscaler,
        run_duration=run_duration,
        working_directory=Path(working_directory),
        s3_bucket=s3_bucket,
        aws_region=aws_region,
        var_files=list(var_file),
        variables=list(variables),
        auto_approve=auto_approve,
        runner_version=runner_version,
        health_timeout=health_timeout,
        rollout_timeout=rollout_timeout,
        cluster_cidr_range=cluster_cidr_range,
        role_params=role_params,
        random_seed=random_seed,
        ebs_wait=ebs_wait,
        rerun_from_failed=rerun_from_failed,
        halt_on_error=halt_on_error,
        max_trial_retries=max_trial_retries,
        spot_enabled=spot_enabled,
    )

    # Instantiate orchestrator and run
    orchestrator = ExperimentOrchestrator(config=config, logger=logger)
    exit_code = orchestrator.run()
    sys.exit(exit_code)
