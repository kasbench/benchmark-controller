"""Benchmark-start subcommand - triggers load generation via the Runner API."""

import json
import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient


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
        # Build context objects
        run_ctx = RunContext(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
        )
        trial_ctx = TrialContext(
            run_context=run_ctx,
            trial_identifier=trial_identifier,
            autoscaler="",  # Not needed for this command
        )

        # --- Dry-run mode ---
        if dry_run:
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

        # --- Step 1: Validate run directory and database ---
        if not run_ctx.run_directory.exists():
            raise KasbenchError(
                f"Run directory does not exist: '{run_ctx.run_directory}'. "
                f"Run 'kasbench init' first."
            )

        if not run_ctx.db_path.exists():
            raise KasbenchError(
                f"Database file not found: '{run_ctx.db_path}'. "
                f"Run 'kasbench init' first."
            )

        db = DatabaseManager(run_ctx.db_path)
        if not db.verify_schema():
            raise KasbenchError(
                f"Database at '{run_ctx.db_path}' does not contain required tables "
                f"(trials, events). Run 'kasbench init' first."
            )

        # --- Step 2: Load trial config ---
        trial_config = load_trial_config(trial_ctx)
        log_step(logger, "load_trial_config", "success",
                 path=str(trial_ctx.output_directory / "trial_config.json"))

        # --- Step 3: Look up trial in database ---
        trial = db.get_trial_by_identifiers(run_identifier, trial_identifier)
        if trial is None:
            raise KasbenchError(
                f"No trial found with run_identifier='{run_identifier}' and "
                f"trial_identifier='{trial_identifier}'. "
                f"Has build-infrastructure been run for this trial?"
            )
        trial_id = trial["trial_id"]
        log_step(logger, "get_trial_by_identifiers", "success", trial_id=trial_id)

        # --- Step 4: POST /start ---
        runner = RunnerAPIClient(
            base_url=f"http://{trial_config.benchmark_runner_public_ip}:8080"
        )
        runner.start(
            benchmark_length_minutes=benchmark_length_minutes,
            role_params=parsed_role_params,
        )
        log_step(logger, "post_start", "success",
                 endpoint="/start")

        # --- Step 5: Record benchmark_start_time in database ---
        db.record_benchmark_start_time(trial_id)
        log_step(logger, "record_benchmark_start_time", "success", trial_id=trial_id)

        # --- Step 6: Insert event for benchmark start ---
        db.insert_event(
            trial_id=trial_id,
            event_type="benchmark_start",
            event_message="Benchmark load generation started",
        )
        log_step(logger, "insert_event", "success",
                 event_type="benchmark_start", trial_id=trial_id)

        # --- Step 7: Exit success ---
        log_step(logger, "benchmark_start_complete", "success")
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "benchmark_start_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
