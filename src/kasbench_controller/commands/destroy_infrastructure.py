"""Destroy-infrastructure subcommand - tears down AWS infrastructure for a benchmark trial."""

import sys
import time
import traceback
from pathlib import Path

import click

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient
from kasbench_controller.tofu import TofuRunner


@click.command("destroy-infrastructure")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--trial-identifier", required=True, type=str, help="Identifier for this trial")
@click.option("--auto-approve", is_flag=True, default=False, help="Skip interactive approval for tofu destroy")
@click.option("--var-file", multiple=True, type=str, help="Var-file arguments for tofu destroy")
@click.option("--var", "variables", multiple=True, type=str, help="Variable arguments for tofu destroy")
@click.option("--no-apply", is_flag=True, default=False, help="Skip the tofu destroy step")
@click.option("--ebs-wait", default=300, type=int, help="Seconds to wait for EBS volume detachment (default 300)")
@click.pass_context
def destroy_infrastructure_cmd(
    ctx: click.Context,
    working_directory: str,
    run_identifier: str,
    trial_identifier: str,
    auto_approve: bool,
    var_file: tuple[str, ...],
    variables: tuple[str, ...],
    no_apply: bool,
    ebs_wait: int,
) -> None:
    """Tear down AWS infrastructure for a benchmark trial via Open Tofu."""
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
            autoscaler="",  # Not needed for destroy
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
            log_dry_run(logger, "record_cleanup_start_time", {
                "trial_id": "pending",
            })
            log_dry_run(logger, "runner_shutdown", {
                "endpoint": "/shutdown",
            })
            log_dry_run(logger, "ebs_wait", {
                "duration_seconds": ebs_wait,
                "progress_interval": 30,
            })
            if not no_apply:
                log_dry_run(logger, "tofu_destroy", {
                    "cwd": str(trial_ctx.tofu_directory),
                    "var_files": list(var_file),
                    "variables": list(variables),
                    "auto_approve": auto_approve,
                })
            log_dry_run(logger, "record_cleanup_end_time", {
                "trial_id": "pending",
            })
            log_step(logger, "destroy_infrastructure_complete", "success", dry_run=True)
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
                var_files=list(var_file),
                variables=list(variables),
                run_id=trial_identifier,
                auto_approve=auto_approve,
            )
            db.insert_event(trial_id, "tofu_destroy", "Tofu destroy completed successfully")
            log_step(logger, "tofu_destroy", "success", cwd=str(trial_ctx.tofu_directory))
        else:
            db.insert_event(trial_id, "tofu_destroy_skipped", "--no-apply flag set, skipping tofu destroy")
            log_step(logger, "tofu_destroy_skipped", "success", reason="--no-apply flag set")

        # --- Step 8: Record cleanup_end_time ---
        db.record_cleanup_end_time(trial_id)
        db.insert_event(trial_id, "cleanup_end", "Destroy infrastructure flow completed")
        log_step(logger, "record_cleanup_end_time", "success", trial_id=trial_id)

        # --- Step 9: Exit success ---
        log_step(logger, "destroy_infrastructure_complete", "success")
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "destroy_infrastructure_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)


def _ebs_wait_loop(logger, duration_seconds: int) -> None:
    """Sleep for the specified duration, printing progress every 30 seconds.

    Args:
        logger: The structlog BoundLogger instance.
        duration_seconds: Total seconds to wait.
    """
    elapsed = 0
    interval = 30

    click.echo(f"Waiting {duration_seconds}s for EBS volumes to detach...")

    while elapsed < duration_seconds:
        remaining = duration_seconds - elapsed
        sleep_time = min(interval, remaining)
        time.sleep(sleep_time)
        elapsed += sleep_time
        remaining_after = duration_seconds - elapsed
        if remaining_after > 0:
            click.echo(f"  EBS wait: {remaining_after}s remaining...")
        else:
            click.echo("  EBS wait complete.")
