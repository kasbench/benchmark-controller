"""Benchmark-monitor service - core logic for polling Runner API status.

Extracted from commands/benchmark_monitor.py so it can be called programmatically
by the experiment orchestrator without Click/sys.exit dependencies.
"""

import time
from pathlib import Path

import structlog

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError, RunnerAPIError
from kasbench_controller.logging import log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient


def run_benchmark_monitor(
    working_directory: Path,
    run_identifier: str,
    trial_identifier: str,
    timeout: int,
    logger: structlog.BoundLogger,
    interval: int = 30,
    verbose: bool = False,
) -> str:
    """Execute benchmark-monitor logic. Returns final status ("success" or "failed").

    Polls the Runner API GET /status endpoint at the configured interval until
    the benchmark reaches a terminal state or the timeout expires.

    Args:
        working_directory: Top-level working directory for the experiment.
        run_identifier: Identifier for this experimental run.
        trial_identifier: Identifier for this trial.
        timeout: Maximum monitoring time in minutes.
        logger: Structured logger instance.
        interval: Status check interval in seconds (default 30).
        verbose: If True, log status messages during polling.

    Returns:
        The final benchmark status string: "success" or "failed".

    Raises:
        KasbenchError: On timeout or API errors during polling.
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

    # Step 1: Load trial config
    trial_config = load_trial_config(trial_ctx)
    log_step(logger, "load_trial_config", "success",
             benchmark_runner_ip=trial_config.benchmark_runner_public_ip)

    # Step 2: Look up trial in database
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

    # Step 3: Poll GET /status at configured interval
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
            raise KasbenchError(
                f"Benchmark monitoring timed out after {timeout} minutes."
            )

        # Poll status
        try:
            response = api.status()
        except RunnerAPIError as e:
            log_step(logger, "benchmark_monitor_poll_error", "failure",
                     error=str(e), status_code=e.status_code)
            raise KasbenchError(
                f"Status polling failed: {e}"
            ) from e

        status_data = response.json()
        current_status = status_data.get("status", "unknown")

        # Verbose output
        if verbose:
            elapsed_min = elapsed / 60
            logger.info(
                "benchmark_monitor_poll",
                elapsed_minutes=f"{elapsed_min:.1f}",
                timeout_minutes=timeout,
                status=current_status,
            )

        # Check for terminal states
        if current_status in ("success", "failed"):
            db.record_benchmark_end_time(trial_id)
            log_step(logger, "benchmark_monitor_complete", "success",
                     final_status=current_status, elapsed_seconds=elapsed)
            return current_status

        # Status is still running — wait before next poll
        time.sleep(interval)
