"""Benchmark-postprocessing service - core logic for shutdown and sequential data exports.

Extracted from commands/benchmark_postprocessing.py so it can be called programmatically
by the experiment orchestrator without Click/sys.exit dependencies.
"""

from pathlib import Path

import structlog

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError, RunnerAPIError
from kasbench_controller.logging import log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient


EXPORT_TYPES = ["metrics", "metadata", "prometheus/tsdb", "output", "db", "roundtrip"]


def run_benchmark_postprocessing(
    working_directory: Path,
    run_identifier: str,
    trial_identifier: str,
    logger: structlog.BoundLogger,
) -> None:
    """Execute benchmark-postprocessing logic: post-benchmark snapshot and sequential exports.

    Args:
        working_directory: Top-level working directory for the experiment.
        run_identifier: Identifier for this experimental run.
        trial_identifier: Identifier for this trial.
        logger: Structured logger instance.

    Raises:
        KasbenchError: On any failure (missing config, DB issues, API errors).
    """
    # Build context objects
    run_ctx = RunContext(
        working_directory=working_directory,
        run_identifier=run_identifier,
    )
    trial_ctx = TrialContext(
        run_context=run_ctx,
        trial_identifier=trial_identifier,
        autoscaler="",  # not needed for postprocessing
    )

    # Step 1: Load trial config (prerequisite check)
    trial_config = load_trial_config(trial_ctx)
    log_step(logger, "load_trial_config", "success",
             path=str(trial_ctx.output_directory / "trial_config.json"))

    # Step 2: Look up trial in database
    db = DatabaseManager(run_ctx.db_path)
    trial = db.get_trial_by_identifiers(run_identifier, trial_identifier)
    if trial is None:
        raise KasbenchError(
            f"No trial found for run_identifier='{run_identifier}' and "
            f"trial_identifier='{trial_identifier}' in database."
        )
    trial_id = trial["trial_id"]
    log_step(logger, "lookup_trial", "success", trial_id=trial_id)

    # Step 3: Initialize Runner API client
    base_url = f"http://{trial_config.benchmark_runner_public_ip}:8080"
    runner = RunnerAPIClient(base_url=base_url)

    # Step 4: Post-benchmark snapshot
    runner.snapshot("post")
    log_step(logger, "snapshot", "success", phase="post")
    db.insert_event(trial_id, "snapshot", "Post-benchmark snapshot taken")

    # Step 5: Sequential exports
    for export_type in EXPORT_TYPES:
        try:
            runner.export(export_type, timeout=180.0)
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

    # Step 6: Final event
    db.insert_event(trial_id, "postprocessing_complete", "All postprocessing steps completed")
    log_step(logger, "benchmark_postprocessing_complete", "success")
