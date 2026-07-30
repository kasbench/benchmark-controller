"""Benchmark-monitor subcommand - polls Runner API status until benchmark completes or times out."""

import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext
from kasbench_controller.services.benchmark_monitor_service import run_benchmark_monitor


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
        # --- Dry-run mode ---
        if dry_run:
            run_ctx = RunContext(
                working_directory=Path(working_directory),
                run_identifier=run_identifier,
            )
            trial_ctx = TrialContext(
                run_context=run_ctx,
                trial_identifier=trial_identifier,
                autoscaler="",
            )
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

        # --- Delegate to service ---
        final_status = run_benchmark_monitor(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
            trial_identifier=trial_identifier,
            timeout=timeout,
            logger=logger,
            interval=interval,
            verbose=verbose,
        )

        # Both "success" and "failed" are valid terminal states — exit cleanly
        if verbose:
            click.echo(f"Benchmark completed with status: {final_status}")
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "benchmark_monitor_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
