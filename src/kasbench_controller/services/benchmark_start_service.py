"""Benchmark-start service - core logic for triggering load generation.

Extracted from commands/benchmark_start.py so it can be called programmatically
by the experiment orchestrator without Click/sys.exit dependencies.
"""

from pathlib import Path

import structlog

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient


def run_benchmark_start(
    working_directory: Path,
    run_identifier: str,
    trial_identifier: str,
    role_params: dict | None,
    logger: structlog.BoundLogger,
    benchmark_length_minutes: int | None = None,
) -> None:
    """Execute benchmark-start logic: POST /start to the Runner API.

    Args:
        working_directory: Top-level working directory for the experiment.
        run_identifier: Identifier for this experimental run.
        trial_identifier: Identifier for this trial.
        role_params: Per-role load generation overrides (already parsed/validated).
        logger: Structured logger instance.
        benchmark_length_minutes: Override benchmark duration in minutes (>=1).
            If None, uses the value from /initialize.

    Raises:
        KasbenchError: On any failure (missing dirs, DB issues, API errors).
    """
    # Build context objects
    run_ctx = RunContext(
        working_directory=working_directory,
        run_identifier=run_identifier,
    )
    trial_ctx = TrialContext(
        run_context=run_ctx,
        trial_identifier=trial_identifier,
        autoscaler="",  # Not needed for this command
    )

    # Step 1: Validate run directory and database
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

    # Step 2: Load trial config
    trial_config = load_trial_config(trial_ctx)
    log_step(logger, "load_trial_config", "success",
             path=str(trial_ctx.output_directory / "trial_config.json"))

    # Step 3: Look up trial in database
    trial = db.get_trial_by_identifiers(run_identifier, trial_identifier)
    if trial is None:
        raise KasbenchError(
            f"No trial found with run_identifier='{run_identifier}' and "
            f"trial_identifier='{trial_identifier}'. "
            f"Has build-infrastructure been run for this trial?"
        )
    trial_id = trial["trial_id"]
    log_step(logger, "get_trial_by_identifiers", "success", trial_id=trial_id)

    # Step 4: POST /start
    runner = RunnerAPIClient(
        base_url=f"http://{trial_config.benchmark_runner_public_ip}:8080"
    )
    runner.start(
        benchmark_length_minutes=benchmark_length_minutes,
        role_params=role_params,
    )
    log_step(logger, "post_start", "success", endpoint="/start")

    # Step 5: Update trial status to RUNNING
    db.update_trial_status(trial_id, "RUNNING")
    log_step(logger, "update_trial_status", "success", trial_id=trial_id, status="RUNNING")

    # Step 6: Record benchmark_start_time in database
    db.record_benchmark_start_time(trial_id)
    log_step(logger, "record_benchmark_start_time", "success", trial_id=trial_id)

    # Step 7: Insert event for benchmark start
    db.insert_event(
        trial_id=trial_id,
        event_type="benchmark_start",
        event_message="Benchmark load generation started",
    )
    log_step(logger, "insert_event", "success",
             event_type="benchmark_start", trial_id=trial_id)

    log_step(logger, "benchmark_start_complete", "success")
