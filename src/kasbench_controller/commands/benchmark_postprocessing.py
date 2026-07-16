"""Benchmark-postprocessing subcommand - triggers shutdown and sequential data exports."""

import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError, RunnerAPIError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient


EXPORT_TYPES = ["metrics", "metadata", "tsdb", "output", "db"]


@click.command("benchmark-postprocessing")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--trial-identifier", required=True, type=str, help="Identifier for this trial")
@click.pass_context
def benchmark_postprocessing_cmd(
    ctx: click.Context,
    working_directory: str,
    run_identifier: str,
    trial_identifier: str,
) -> None:
    """Trigger shutdown and export benchmark artifacts via the Runner API."""
    logger = ctx.obj["logger"]
    dry_run = ctx.obj["dry_run"]

    try:
        # Build context objects
        run_ctx = RunContext(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
        )
        trial_ctx = TrialContext(
            run_context=run_ctx,
            trial_identifier=trial_identifier,
            autoscaler="",  # not needed for postprocessing
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
            log_dry_run(logger, "shutdown", {
                "endpoint": "/shutdown",
            })
            for export_type in EXPORT_TYPES:
                log_dry_run(logger, f"export_{export_type}", {
                    "endpoint": f"/{export_type}/export",
                })
            log_step(logger, "benchmark_postprocessing_complete", "success", dry_run=True)
            sys.exit(0)

        # --- Step 1: Load trial config (prerequisite check) ---
        trial_config = load_trial_config(trial_ctx)
        log_step(logger, "load_trial_config", "success",
                 path=str(trial_ctx.output_directory / "trial_config.json"))

        # --- Step 2: Look up trial in database ---
        db = DatabaseManager(run_ctx.db_path)
        trial = db.get_trial_by_identifiers(run_identifier, trial_identifier)
        if trial is None:
            raise KasbenchError(
                f"No trial found for run_identifier='{run_identifier}' and "
                f"trial_identifier='{trial_identifier}' in database."
            )
        trial_id = trial["trial_id"]
        log_step(logger, "lookup_trial", "success", trial_id=trial_id)

        # --- Step 3: Initialize Runner API client ---
        base_url = f"http://{trial_config.benchmark_runner_public_ip}:8080"
        runner = RunnerAPIClient(base_url=base_url)

        # --- Step 4: Sequential exports ---
        for export_type in EXPORT_TYPES:
            try:
                runner.export(export_type)
            except RunnerAPIError as e:
                raise KasbenchError(
                    f"Export failed for '{export_type}': {e}"
                ) from e
            db.insert_event(
                trial_id,
                f"postprocessing_export_{export_type}",
                f"Export '{export_type}' completed successfully",
            )
            log_step(logger, f"export_{export_type}", "success")

       # --- Step 5: POST /shutdown ---
        try:
            runner.shutdown()
        except RunnerAPIError as e:
            raise KasbenchError(
                f"Shutdown request failed: {e}"
            ) from e
        db.insert_event(trial_id, "postprocessing_shutdown", "Shutdown request successful")
        log_step(logger, "shutdown", "success")


        # --- Step 6: Final event and exit ---
        db.insert_event(trial_id, "postprocessing_complete", "All postprocessing steps completed")
        log_step(logger, "benchmark_postprocessing_complete", "success")
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "benchmark_postprocessing_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
