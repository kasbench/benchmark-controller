"""Build-infrastructure service - core logic for provisioning AWS infrastructure.

Extracted from commands/build_infrastructure.py so it can be called programmatically
by the experiment orchestrator without Click/sys.exit dependencies.
"""

import json
import shutil
from pathlib import Path

import structlog

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import DuplicateTrialError, KasbenchError
from kasbench_controller.logging import log_step
from kasbench_controller.models import RunContext, TrialConfig, TrialContext, save_trial_config
from kasbench_controller.output_parser import parse_tofu_outputs
from kasbench_controller.repository import RepositoryDownloader
from kasbench_controller.s3_uploader import S3Uploader
from kasbench_controller.tofu import TofuRunner


def run_build_infrastructure(
    working_directory: Path,
    run_identifier: str,
    trial_identifier: str,
    autoscaler: str,
    aws_region: str,
    s3_bucket: str,
    run_duration: int,
    auto_approve: bool,
    var_files: list[str],
    variables: list[str],
    logger: structlog.BoundLogger,
    force: bool = False,
    no_apply: bool = False,
) -> None:
    """Execute build-infrastructure logic: provision AWS infrastructure for a benchmark trial.

    Args:
        working_directory: Top-level working directory for the experiment.
        run_identifier: Identifier for this experimental run.
        trial_identifier: Identifier for this trial.
        autoscaler: Autoscaler to benchmark.
        aws_region: AWS region for infrastructure deployment.
        s3_bucket: S3 bucket for artifact storage.
        run_duration: Benchmark run duration in minutes.
        auto_approve: Skip interactive approval for tofu apply.
        var_files: Var-file arguments for tofu apply.
        variables: Variable arguments for tofu apply.
        logger: Structured logger instance.
        force: If True, overwrite existing trial directory.
        no_apply: If True, stop after tofu init without applying.

    Raises:
        KasbenchError: On any failure (directory issues, DB errors, tofu failures, etc.).
    """
    # Build context objects
    run_ctx = RunContext(
        working_directory=working_directory,
        run_identifier=run_identifier,
    )
    trial_ctx = TrialContext(
        run_context=run_ctx,
        trial_identifier=trial_identifier,
        autoscaler=autoscaler,
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

    # --- Step 2: Handle trial directory ---
    if trial_ctx.trial_directory.exists():
        if not force:
            raise KasbenchError(
                f"Trial directory already exists: '{trial_ctx.trial_directory}'. "
                f"Use --force to overwrite."
            )
        # --force: delete existing trial directory
        try:
            shutil.rmtree(trial_ctx.trial_directory)
            log_step(logger, "remove_existing_trial_directory", "success",
                     path=str(trial_ctx.trial_directory))
        except OSError as e:
            raise KasbenchError(
                f"Failed to remove existing trial directory "
                f"'{trial_ctx.trial_directory}': {e}"
            ) from e

    # Create trial directory
    try:
        trial_ctx.trial_directory.mkdir(parents=True, exist_ok=False)
        log_step(logger, "create_trial_directory", "success",
                 path=str(trial_ctx.trial_directory))
    except OSError as e:
        raise KasbenchError(
            f"Failed to create trial directory '{trial_ctx.trial_directory}': {e}"
        ) from e

    # Create output subdirectory
    try:
        trial_ctx.output_directory.mkdir(parents=True, exist_ok=False)
        log_step(logger, "create_output_directory", "success",
                 path=str(trial_ctx.output_directory))
    except OSError as e:
        raise KasbenchError(
            f"Failed to create output directory '{trial_ctx.output_directory}': {e}"
        ) from e

    # --- Step 3: Download repository ---
    downloader = RepositoryDownloader(
        target_dir=trial_ctx.tofu_directory,
        dry_run=False,
        logger=logger,
    )
    downloader.download_and_extract()
    log_step(logger, "download_repository", "success",
             target_dir=str(trial_ctx.tofu_directory))

    # --- Step 4: Check for duplicate trial ---
    if db.check_duplicate_trial(run_identifier, trial_identifier):
        raise DuplicateTrialError(
            f"Trial with run_identifier='{run_identifier}' and "
            f"trial_identifier='{trial_identifier}' already exists"
        )

    # --- Step 5: Insert trial record ---
    trial_id = db.insert_trial(run_identifier, trial_identifier, autoscaler)
    log_step(logger, "insert_trial", "success", trial_id=trial_id)

    # --- Step 6: Run tofu init ---
    tofu = TofuRunner(working_dir=trial_ctx.tofu_directory, dry_run=False)
    tofu.init()
    log_step(logger, "tofu_init", "success", cwd=str(trial_ctx.tofu_directory))

    # --- Step 7: If --no-apply, exit early ---
    if no_apply:
        log_step(logger, "early_termination", "success",
                 reason="--no-apply flag provided")
        return

    # --- Step 8: If not --auto-approve, run plan and prompt ---
    if not auto_approve:
        plan_result = tofu.plan(
            var_files=list(var_files),
            variables=list(variables),
            run_id=trial_identifier,
        )
        log_step(logger, "tofu_plan", "success", cwd=str(trial_ctx.tofu_directory))

        # Import click here only for interactive prompting
        import click

        # Display plan output
        click.echo("\n--- Tofu Plan Output ---")
        click.echo(plan_result.stdout)
        if plan_result.stderr:
            click.echo(plan_result.stderr)
        click.echo("--- End Plan Output ---\n")

        # Prompt for approval
        if not click.confirm("Do you want to apply this plan?"):
            raise KasbenchError("User declined the apply")

    # --- Step 9: Record infra_start_time ---
    db.record_infra_start_time(trial_id)
    log_step(logger, "record_infra_start_time", "success", trial_id=trial_id)

    # --- Step 10: Run tofu apply ---
    tofu.apply(
        var_files=list(var_files),
        variables=list(variables),
        run_id=trial_identifier,
        auto_approve=auto_approve,
    )
    log_step(logger, "tofu_apply", "success", cwd=str(trial_ctx.tofu_directory))

    # --- Step 11: Capture outputs ---
    output = tofu.output_json()
    log_step(logger, "tofu_output", "success")

    # --- Step 12: Parse outputs ---
    parsed = parse_tofu_outputs(output)
    public_ip = parsed.benchmark_runner_public_ip
    key_pair_name = parsed.ssh_key_pair_name

    # --- Step 13: Write outputs to file ---
    output_file = trial_ctx.output_directory / "tofu_outputs.json"
    try:
        output_file.write_text(json.dumps(output, indent=2))
        log_step(logger, "write_outputs", "success", path=str(output_file))
    except OSError as e:
        raise KasbenchError(
            f"Failed to write tofu outputs to '{output_file}': {e}"
        ) from e

    # --- Step 14: Update trial record ---
    db.update_trial_after_apply(trial_id, public_ip, key_pair_name)
    log_step(logger, "update_trial_record", "success", trial_id=trial_id,
             status="INIT", public_ip=public_ip, key_pair_name=key_pair_name)

    # --- Step 15: Upload trial artifacts to S3 ---
    s3 = S3Uploader(bucket=s3_bucket, region=aws_region, dry_run=False)
    s3.upload_trial_artifacts(trial_ctx, run_identifier, trial_identifier)
    log_step(logger, "s3_upload_trial_artifacts", "success",
             bucket=s3_bucket, region=aws_region)

    # --- Step 16: Build and save trial config ---
    trial_config = TrialConfig(
        aws_region=aws_region,
        s3_bucket=s3_bucket,
        run_duration=run_duration,
        benchmark_runner_public_ip=parsed.benchmark_runner_public_ip or "",
        ssh_key_pair_name=parsed.ssh_key_pair_name or "",
        control_plane_private_ip=parsed.control_plane_private_ip or "",
        amd_worker_private_ips=parsed.amd_worker_private_ips,
        arm_worker_private_ips=parsed.arm_worker_private_ips,
        globeco_dns=parsed.globeco_dns or "",
        globeco_port=parsed.globeco_port or 0,
        execution_data_fs=parsed.execution_data_fs or "",
    )
    save_trial_config(trial_ctx, trial_config)
    log_step(logger, "save_trial_config", "success",
             path=str(trial_ctx.output_directory / "trial_config.json"))

    # --- Step 17: Record infra end time ---
    db.record_infra_end_time(trial_id)
    log_step(logger, "record_infra_end_time", "success", trial_id=trial_id)

    # --- Step 18: Done ---
    log_step(logger, "build_infrastructure_complete", "success")
