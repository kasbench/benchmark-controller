"""Build-infrastructure subcommand - provisions AWS infrastructure for a benchmark trial."""

import json
import shutil
import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialConfig, TrialContext, save_trial_config
from kasbench_controller.output_parser import parse_tofu_outputs
from kasbench_controller.repository import RepositoryDownloader
from kasbench_controller.s3_uploader import S3Uploader
from kasbench_controller.tofu import TofuRunner


@click.command("build-infrastructure")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--trial-identifier", required=True, type=str, help="Identifier for this trial")
@click.option("--autoscaler", required=True, type=str, help="Autoscaler to benchmark")
@click.option("--aws-region", default="us-east-1", type=str, help="AWS region for infrastructure deployment")
@click.option("--s3-bucket", required=True, type=str, help="S3 bucket for artifact storage")
@click.option("--run-duration", required=True, type=int, help="Benchmark run duration in minutes")
@click.option("--auto-approve", is_flag=True, default=False, help="Skip interactive approval for tofu apply")
@click.option("--var-file", multiple=True, type=str, help="Var-file arguments for tofu apply")
@click.option("--var", "variables", multiple=True, type=str, help="Variable arguments for tofu apply")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing trial directory")
@click.option("--no-apply", is_flag=True, default=False, help="Stop after tofu init without applying")
@click.pass_context
def build_infrastructure_cmd(
    ctx: click.Context,
    working_directory: str,
    run_identifier: str,
    trial_identifier: str,
    autoscaler: str,
    aws_region: str,
    s3_bucket: str,
    run_duration: int,
    auto_approve: bool,
    var_file: tuple[str, ...],
    variables: tuple[str, ...],
    force: bool,
    no_apply: bool,
) -> None:
    """Provision AWS infrastructure for a benchmark trial via Open Tofu."""
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
            autoscaler=autoscaler,
        )

        # --- Dry-run mode ---
        if dry_run:
            log_dry_run(logger, "validate_run_directory", {
                "path": str(run_ctx.run_directory),
                "db_path": str(run_ctx.db_path),
            })
            log_dry_run(logger, "create_trial_directory", {
                "path": str(trial_ctx.trial_directory),
                "force": force,
            })
            log_dry_run(logger, "create_output_directory", {
                "path": str(trial_ctx.output_directory),
            })
            log_dry_run(logger, "download_repository", {
                "target_dir": str(trial_ctx.tofu_directory),
            })
            log_dry_run(logger, "check_duplicate_trial", {
                "run_identifier": run_identifier,
                "trial_identifier": trial_identifier,
            })
            log_dry_run(logger, "insert_trial", {
                "run_identifier": run_identifier,
                "trial_identifier": trial_identifier,
                "autoscaler": autoscaler,
            })
            log_dry_run(logger, "tofu_init", {
                "cwd": str(trial_ctx.tofu_directory),
            })
            if not no_apply:
                log_dry_run(logger, "tofu_apply", {
                    "cwd": str(trial_ctx.tofu_directory),
                    "var_files": list(var_file),
                    "variables": list(variables),
                    "run_id": trial_identifier,
                    "auto_approve": auto_approve,
                })
                log_dry_run(logger, "capture_outputs", {
                    "output_file": str(trial_ctx.output_directory / "tofu_outputs.json"),
                })
                log_dry_run(logger, "update_trial_record", {
                    "status": "INIT",
                })
                log_dry_run(logger, "s3_upload_trial_artifacts", {
                    "bucket": s3_bucket,
                    "region": aws_region,
                    "run_identifier": run_identifier,
                    "trial_identifier": trial_identifier,
                })
                log_dry_run(logger, "save_trial_config", {
                    "output_directory": str(trial_ctx.output_directory),
                    "aws_region": aws_region,
                    "s3_bucket": s3_bucket,
                    "run_duration": run_duration,
                })
                log_dry_run(logger, "record_infra_end_time", {
                    "trial_id": "pending",
                })
            log_step(logger, "build_infrastructure_complete", "success", dry_run=True)
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
            from kasbench_controller.exceptions import DuplicateTrialError
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
            sys.exit(0)

        # --- Step 8: If not --auto-approve, run plan and prompt ---
        if not auto_approve:
            plan_result = tofu.plan(
                var_files=list(var_file),
                variables=list(variables),
                run_id=trial_identifier,
            )
            log_step(logger, "tofu_plan", "success", cwd=str(trial_ctx.tofu_directory))

            # Display plan output
            click.echo("\n--- Tofu Plan Output ---")
            click.echo(plan_result.stdout)
            if plan_result.stderr:
                click.echo(plan_result.stderr)
            click.echo("--- End Plan Output ---\n")

            # Prompt for approval
            if not click.confirm("Do you want to apply this plan?"):
                log_step(logger, "user_declined_apply", "failure",
                         reason="User declined the apply")
                sys.exit(1)

        # --- Step 9: Record infra_start_time ---
        db.record_infra_start_time(trial_id)
        log_step(logger, "record_infra_start_time", "success", trial_id=trial_id)

        # --- Step 10: Run tofu apply ---
        tofu.apply(
            var_files=list(var_file),
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
        s3 = S3Uploader(bucket=s3_bucket, region=aws_region, dry_run=dry_run)
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
        )
        save_trial_config(trial_ctx, trial_config)
        log_step(logger, "save_trial_config", "success",
                 path=str(trial_ctx.output_directory / "trial_config.json"))

        # --- Step 17: Record infra end time ---
        db.record_infra_end_time(trial_id)
        log_step(logger, "record_infra_end_time", "success", trial_id=trial_id)

        # --- Step 18: Exit success ---
        log_step(logger, "build_infrastructure_complete", "success")
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "build_infrastructure_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
