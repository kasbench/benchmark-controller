"""Build-infrastructure subcommand - provisions AWS infrastructure for a benchmark trial."""

import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext
from kasbench_controller.services.build_infrastructure_service import run_build_infrastructure


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
        # --- Dry-run mode ---
        if dry_run:
            run_ctx = RunContext(
                working_directory=Path(working_directory),
                run_identifier=run_identifier,
            )
            trial_ctx = TrialContext(
                run_context=run_ctx,
                trial_identifier=trial_identifier,
                autoscaler=autoscaler,
            )

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

        run_build_infrastructure(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
            trial_identifier=trial_identifier,
            autoscaler=autoscaler,
            aws_region=aws_region,
            s3_bucket=s3_bucket,
            run_duration=run_duration,
            auto_approve=auto_approve,
            var_files=list(var_file),
            variables=list(variables),
            logger=logger,
            force=force,
            no_apply=no_apply,
        )
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "build_infrastructure_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
