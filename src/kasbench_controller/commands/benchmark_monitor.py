"""Benchmark-monitor subcommand - polls Runner API status until benchmark completes or times out."""

import sys
import time
import traceback
from pathlib import Path

import click

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError, RunnerAPIError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient


@click.command("benchmark-monitor")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--trial-identifier", required=True, type=str, help="Identifier for this trial")
@click.option("--timeout", required=True, type=int, help="Maximum monitoring time in minutes")
@click.option("--interval", default=30, type=int, help="Status check interval in seconds")
@click.option("--verbose", is_flag=True, default=False, help="Print status messages during polling")
@click.pass_context
def benchmark_monitor_cmd(
    ctx: click.Context,
    working_directory: str,
    run_identifier: str,
    trial_identifier: str,
    timeout: int,
    interval: int,
    verbose: bool,
) -> None:
    """Poll the Runner API until the benchmark completes or times out."""
    logger = ctx.obj["logger"]
    dry_run = ctx.obj["dry_run"]

    try:
        # Build context objects
        run_ctx = RunContext(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
        )
        # TrialContext requires autoscaler but we only need paths; use empty string placeholder
        trial_ctx = TrialContext(
            run_context=run_ctx,
            trial_identifier=trial_identifier,
            autoscaler="",
        )

        # --- Dry-run mode ---
        if dry_run:
            log_dry_run(logger, "load_trial_config", {
                "path": str(trial_ctx.output_directory / "trial_config.json"),
            })
            log_dry_run(logger, "lookup_trial", {
                "run_identifier": run_identifier,
                "trial_identifier": trial_identifier,
            })
            log_dry_run(logger, "benchmark_monitor_poll", {
                "timeout_minutes": timeout,
                "interval_seconds": interval,
                "verbose": verbose,
            })
            log_dry_run(logger, "record_benchmark_end_time", {
                "trial_id": "pending",
            })
            log_step(logger, "benchmark_monitor_complete", "success", dry_run=True)
            sys.exit(0)

        # --- Step 1: Load trial config ---
        trial_config = load_trial_config(trial_ctx)
        log_step(logger, "load_trial_config", "success",
                 benchmark_runner_ip=trial_config.benchmark_runner_public_ip)

        # --- Step 2: Look up trial in database ---
        db = DatabaseManager(run_ctx.db_path)
        trial_record = db.get_trial_by_identifiers(run_identifier, trial_identifier)
        if trial_record is None:
            raise KasbenchError(
                f"No trial found for run_identifier='{run_identifier}' and "
                f"trial_identifier='{trial_identifier}'. "
                f"Has build-infrastructure been run for this trial?"
            )
        trial_id = trial_record["trial_id"]
        log_step(logger, "lookup_trial", "success", trial_id=trial_id)

        # --- Step 3: Poll GET /status at configured interval ---
        base_url = f"http://{trial_config.benchmark_runner_public_ip}:8080"
        api = RunnerAPIClient(base_url=base_url)

        timeout_seconds = timeout * 60
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time

            # Check timeout
            if elapsed >= timeout_seconds:
                log_step(logger, "benchmark_monitor_timeout", "failure",
                         elapsed_seconds=elapsed, timeout_minutes=timeout)
                click.echo(
                    f"Error: Benchmark monitoring timed out after {timeout} minutes.",
                    err=True,
                )
                sys.exit(1)

            # Poll status
            try:
                response = api.status()
            except RunnerAPIError as e:
                log_step(logger, "benchmark_monitor_poll_error", "failure",
                         error=str(e), status_code=e.status_code)
                click.echo(
                    f"Error: Status polling failed: {e}",
                    err=True,
                )
                sys.exit(1)

            status_data = response.json()
            current_status = status_data.get("status", "unknown")

            # Verbose output
            if verbose:
                elapsed_min = elapsed / 60
                click.echo(
                    f"[{elapsed_min:.1f}m/{timeout}m] Benchmark status: {current_status}"
                )

            # Check for terminal states
            if current_status in ("success", "failed"):
                db.record_benchmark_end_time(trial_id)
                log_step(logger, "benchmark_monitor_complete", "success",
                         final_status=current_status, elapsed_seconds=elapsed)
                sys.exit(0)

            # Status is still running — wait before next poll
            if current_status == "running":
                time.sleep(interval)
                continue

            # Unexpected status value — treat as running and continue
            if verbose:
                click.echo(f"  Unexpected status value: '{current_status}', continuing to poll...")
            time.sleep(interval)

    except KasbenchError as e:
        log_step(logger, "benchmark_monitor_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
