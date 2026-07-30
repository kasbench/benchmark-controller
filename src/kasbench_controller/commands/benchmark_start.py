"""Benchmark-start subcommand - triggers load generation via the Runner API."""

import json
import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext
from kasbench_controller.services.benchmark_start_service import run_benchmark_start


def _parse_role_params(value: str) -> dict:
    """Parse a JSON string into a roleParams dictionary.

    Validates that the structure matches the expected schema:
    keys are role names, values are objects with baseLoadIntensity,
    baseDelayPercentage, and spawnRate.

    Args:
        value: JSON string representing role parameters.

    Returns:
        Parsed dictionary.

    Raises:
        click.BadParameter: If the JSON is invalid or doesn't match schema.
    """
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON for --role-params: {e}")

    if not isinstance(parsed, dict):
        raise click.BadParameter("--role-params must be a JSON object")

    required_keys = {"baseLoadIntensity", "baseDelayPercentage", "spawnRate"}
    for role_name, params in parsed.items():
        if not isinstance(params, dict):
            raise click.BadParameter(
                f"--role-params: value for role '{role_name}' must be an object"
            )
        missing = required_keys - set(params.keys())
        if missing:
            raise click.BadParameter(
                f"--role-params: role '{role_name}' is missing keys: {sorted(missing)}"
            )

    return parsed


@click.command("benchmark-start")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--trial-identifier", required=True, type=str, help="Identifier for this trial")
@click.option(
    "--benchmark-length-minutes",
    required=False,
    type=int,
    default=None,
    help="Override benchmark duration in minutes (>=1). If omitted, uses the value from /initialize.",
)
@click.option(
    "--role-params",
    required=False,
    type=str,
    default=None,
    help='Per-role load generation overrides as a JSON string. Example: \'{"trader":{"baseLoadIntensity":50,"baseDelayPercentage":80,"spawnRate":5}}\'',
)
@click.pass_context
def benchmark_start_cmd(
    ctx: click.Context,
    working_directory: str,
    run_identifier: str,
    trial_identifier: str,
    benchmark_length_minutes: int | None,
    role_params: str | None,
) -> None:
    """Start benchmark load generation via the Runner API."""
    logger = ctx.obj["logger"]
    dry_run = ctx.obj["dry_run"]

    # Validate and parse role_params JSON if provided
    parsed_role_params: dict | None = None
    if role_params is not None:
        parsed_role_params = _parse_role_params(role_params)

    # Validate benchmark_length_minutes
    if benchmark_length_minutes is not None and benchmark_length_minutes < 1:
        click.echo("Error: --benchmark-length-minutes must be >= 1.", err=True)
        sys.exit(1)

    try:
        # --- Dry-run mode ---
        if dry_run:
            run_ctx = RunContext(
                working_directory=Path(working_directory),
                run_identifier=run_identifier,
            )
            trial_ctx = TrialContext(
                run_context=run_ctx,
                trial_identifier=trial_identifier,
                autoscaler="",  # Not needed for this command
            )
            log_dry_run(logger, "load_trial_config", {
                "path": str(trial_ctx.output_directory / "trial_config.json"),
            })
            log_dry_run(logger, "get_trial_by_identifiers", {
                "run_identifier": run_identifier,
                "trial_identifier": trial_identifier,
            })
            start_body: dict = {}
            if benchmark_length_minutes is not None:
                start_body["benchmarkLengthMinutes"] = benchmark_length_minutes
            if parsed_role_params is not None:
                start_body["roleParams"] = parsed_role_params
            log_dry_run(logger, "post_start", {
                "endpoint": "/start",
                "body": json.dumps(start_body),
            })
            log_dry_run(logger, "record_benchmark_start_time", {
                "trial_id": "pending",
            })
            log_dry_run(logger, "insert_event", {
                "event_type": "benchmark_start",
                "event_message": "Benchmark load generation started",
            })
            log_step(logger, "benchmark_start_complete", "success", dry_run=True)
            sys.exit(0)

        # --- Execute via service ---
        run_benchmark_start(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
            trial_identifier=trial_identifier,
            role_params=parsed_role_params,
            logger=logger,
            benchmark_length_minutes=benchmark_length_minutes,
        )
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "benchmark_start_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
