"""Init subcommand - initializes a new experimental run."""

import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext
from kasbench_controller.services.init_service import run_init


@click.command("init")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing run directory")
@click.pass_context
def init_cmd(ctx: click.Context, working_directory: str, run_identifier: str, force: bool) -> None:
    """Initialize a new experimental run with a clean workspace and database."""
    logger = ctx.obj["logger"]
    dry_run = ctx.obj["dry_run"]

    try:
        if dry_run:
            run_ctx = RunContext(
                working_directory=Path(working_directory),
                run_identifier=run_identifier,
            )
            log_dry_run(logger, "create_working_directory", {
                "path": str(run_ctx.working_directory),
                "parents": True,
                "exist_ok": True,
            })
            log_dry_run(logger, "create_run_directory", {
                "path": str(run_ctx.run_directory),
                "force": force,
            })
            log_dry_run(logger, "create_database", {
                "path": str(run_ctx.db_path),
                "tables": ["trials", "events"],
            })
            log_step(logger, "init_complete", "success", dry_run=True)
            sys.exit(0)

        run_init(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
            logger=logger,
            force=force,
        )
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "init_failed", "failure", error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure", error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
