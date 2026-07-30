"""Shutdown subcommand - sends a shutdown request to the Runner API."""

import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext
from kasbench_controller.services.shutdown_service import run_shutdown


@click.command("shutdown")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--trial-identifier", required=True, type=str, help="Identifier for this trial")
@click.pass_context
def shutdown_cmd(
    ctx: click.Context,
    working_directory: str,
    run_identifier: str,
    trial_identifier: str,
) -> None:
    """Send a shutdown request to the Runner API."""
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
                autoscaler="",  # not needed for shutdown
            )
            log_dry_run(logger, "load_trial_config", {
                "path": str(trial_ctx.output_directory / "trial_config.json"),
            })
            log_dry_run(logger, "lookup_trial", {
                "run_identifier": run_identifier,
                "trial_identifier": trial_identifier,
            })
            log_dry_run(logger, "shutdown", {
                "endpoint": "/shutdown",
            })
            log_step(logger, "shutdown_complete", "success", dry_run=True)
            sys.exit(0)

        run_shutdown(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
            trial_identifier=trial_identifier,
            logger=logger,
        )
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "shutdown_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
