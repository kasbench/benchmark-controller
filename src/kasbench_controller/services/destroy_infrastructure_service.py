"""Destroy-infrastructure service - core logic for tearing down AWS infrastructure.

Extracted from commands/destroy_infrastructure.py so it can be called programmatically
by the experiment orchestrator without Click/sys.exit dependencies.
"""

import shutil
import time
from pathlib import Path

import structlog

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient
from kasbench_controller.tofu import TofuRunner


def run_destroy_infrastructure(
    working_directory: Path,
    run_identifier: str,
    trial_identifier: str,
    auto_approve: bool,
    var_files: list[str],
    variables: list[str],
    ebs_wait: int,
    logger: structlog.BoundLogger,
    no_apply: bool = False,
) -> None:
    """Execute destroy-infrastructure logic: tear down AWS infrastructure for a trial.

    Args:
        working_directory: Top-level working directory for the experiment.
        run_identifier: Identifier for this experimental run.
        trial_identifier: Identifier for this trial.
        auto_approve: Skip interactive approval for tofu destroy.
        var_files: Var-file arguments for tofu destroy.
        variables: Variable arguments for tofu destroy.
        ebs_wait: Seconds to wait for EBS volume detachment.
        logger: Structured logger instance.
        no_apply: If True, skip the tofu destroy step.

    Raises:
        KasbenchError: On any failure (directory issues, DB errors, runner shutdown,
            tofu destroy failures, etc.).
    """
    # Build context objects
    run_ctx = RunContext(
        working_directory=working_directory,
        run_identifier=run_identifier,
    )
    trial_ctx = TrialContext(
        run_context=run_ctx,
        trial_identifier=trial_identifier,
        autoscaler="",  # Not needed for destroy
    )

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
    log_step(logger, "validate_run_directory", "success", path=str(run_ctx.run_directory))

    # --- Step 2: Load trial config ---
    trial_config = load_trial_config(trial_ctx)
    log_step(logger, "load_trial_config", "success",
             benchmark_runner_ip=trial_config.benchmark_runner_public_ip)

    # --- Step 3: Look up trial in database ---
    trial_record = db.get_trial_by_identifiers(run_identifier, trial_identifier)
    if trial_record is None:
        raise KasbenchError(
            f"No trial found with run_identifier='{run_identifier}' and "
            f"trial_identifier='{trial_identifier}' in the database."
        )
    trial_id = trial_record["trial_id"]
    log_step(logger, "lookup_trial", "success", trial_id=trial_id)

    # --- Step 4: Record cleanup_start_time ---
    db.record_cleanup_start_time(trial_id)
    db.insert_event(trial_id, "cleanup_start", "Destroy infrastructure flow started")
    log_step(logger, "record_cleanup_start_time", "success", trial_id=trial_id)

    # --- Step 5: POST /shutdown to runner ---
    runner_url = f"http://{trial_config.benchmark_runner_public_ip}:8080"
    runner = RunnerAPIClient(base_url=runner_url)
    runner.shutdown()
    db.insert_event(trial_id, "runner_shutdown", "Runner shutdown request succeeded")
    log_step(logger, "runner_shutdown", "success", endpoint="/shutdown")

    # --- Step 6: EBS wait ---
    _ebs_wait_loop(logger, ebs_wait)
    db.insert_event(
        trial_id, "ebs_wait_complete",
        f"EBS wait completed after {ebs_wait} seconds"
    )
    log_step(logger, "ebs_wait", "success", duration_seconds=ebs_wait)

    # --- Step 7: Tofu destroy (unless --no-apply) ---
    if not no_apply:
        tofu = TofuRunner(working_dir=trial_ctx.tofu_directory, dry_run=False)
        tofu.destroy(
            var_files=list(var_files),
            variables=list(variables),
            run_id=trial_identifier,
            auto_approve=auto_approve,
        )
        db.insert_event(trial_id, "tofu_destroy", "Tofu destroy completed successfully")
        log_step(logger, "tofu_destroy", "success", cwd=str(trial_ctx.tofu_directory))

        # Remove .terraform directory to reclaim disk space (~1.7 GB).
        # This is safe: tofu.destroy() is synchronous and would have raised
        # TofuError if it failed, so we only reach here on successful destroy.
        _remove_terraform_dir(trial_ctx.tofu_directory, trial_id, db, logger)
    else:
        db.insert_event(trial_id, "tofu_destroy_skipped", "--no-apply flag set, skipping tofu destroy")
        log_step(logger, "tofu_destroy_skipped", "success", reason="--no-apply flag set")

    # --- Step 8: Record cleanup_end_time ---
    db.record_cleanup_end_time(trial_id)
    db.insert_event(trial_id, "cleanup_end", "Destroy infrastructure flow completed")
    log_step(logger, "record_cleanup_end_time", "success", trial_id=trial_id)

    # --- Done ---
    log_step(logger, "destroy_infrastructure_complete", "success")


def _ebs_wait_loop(logger: structlog.BoundLogger, duration_seconds: int) -> None:
    """Sleep for the specified duration, logging progress every 30 seconds.

    Args:
        logger: The structlog BoundLogger instance.
        duration_seconds: Total seconds to wait.
    """
    elapsed = 0
    interval = 30

    log_step(logger, "ebs_wait_start", "info",
             duration_seconds=duration_seconds,
             message=f"Waiting {duration_seconds}s for EBS volumes to detach...")

    while elapsed < duration_seconds:
        remaining = duration_seconds - elapsed
        sleep_time = min(interval, remaining)
        time.sleep(sleep_time)
        elapsed += sleep_time
        remaining_after = duration_seconds - elapsed
        if remaining_after > 0:
            log_step(logger, "ebs_wait_progress", "info",
                     remaining_seconds=remaining_after)
        else:
            log_step(logger, "ebs_wait_done", "info",
                     message="EBS wait complete.")


def _remove_terraform_dir(
    tofu_directory: Path,
    trial_id: int | None,
    db: "DatabaseManager | None",
    logger: structlog.BoundLogger,
) -> None:
    """Remove the .terraform subdirectory to reclaim disk space.

    Safe to call only after tofu destroy has completed successfully.
    The .terraform directory holds downloaded provider binaries (~1.7 GB)
    that are no longer needed once the infrastructure is destroyed.

    Args:
        tofu_directory: Path to the benchmark-infrastructure directory.
        trial_id: Database trial ID (for event logging). May be None if called
            from a context without DB tracking.
        db: DatabaseManager instance. May be None if called from abort tier 2.
        logger: Structured logger instance.
    """
    terraform_dir = tofu_directory / ".terraform"
    if terraform_dir.exists():
        shutil.rmtree(terraform_dir)
        if db is not None and trial_id is not None:
            db.insert_event(trial_id, "terraform_dir_removed", f"Removed {terraform_dir}")
        log_step(logger, "remove_terraform_dir", "success", path=str(terraform_dir))
    else:
        log_step(logger, "remove_terraform_dir", "skipped", path=str(terraform_dir),
                 reason="directory does not exist")
