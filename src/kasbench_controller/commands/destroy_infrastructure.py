"""Destroy-infrastructure subcommand - tears down AWS infrastructure for a benchmark trial."""

import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext
from kasbench_controller.services.destroy_infrastructure_service import run_destroy_infrastructure


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
        # --- Dry-run mode ---
        if dry_run:
            run_ctx = RunContext(
                working_directory=Path(working_directory),
                run_identifier=run_identifier,
            )
            trial_ctx = TrialContext(
                run_context=run_ctx,
                trial_identifier=trial_identifier,
                autoscaler="",
            )
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

        # --- Execute via service function ---
        run_destroy_infrastructure(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
            trial_identifier=trial_identifier,
            auto_approve=auto_approve,
            var_files=list(var_file),
            variables=list(variables),
            ebs_wait=ebs_wait,
            logger=logger,
            no_apply=no_apply,
        )
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "destroy_infrastructure_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
