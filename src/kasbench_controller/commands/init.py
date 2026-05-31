"""Init subcommand - initializes a new experimental run."""

import shutil
import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext


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
        run_ctx = RunContext(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
        )

        if dry_run:
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

        # Create working directory (parents=True, exist_ok=True)
        try:
            run_ctx.working_directory.mkdir(parents=True, exist_ok=True)
            log_step(logger, "create_working_directory", "success", path=str(run_ctx.working_directory))
        except OSError as e:
            raise KasbenchError(
                f"Failed to create working directory '{run_ctx.working_directory}': {e}"
            ) from e

        # Handle existing run directory
        if run_ctx.run_directory.exists():
            if not force:
                raise KasbenchError(
                    f"Run directory already exists: '{run_ctx.run_directory}'. "
                    f"Use --force to overwrite."
                )
            # --force: delete and recreate
            try:
                shutil.rmtree(run_ctx.run_directory)
                log_step(logger, "remove_existing_run_directory", "success", path=str(run_ctx.run_directory))
            except OSError as e:
                raise KasbenchError(
                    f"Failed to remove existing run directory '{run_ctx.run_directory}': {e}"
                ) from e

        # Create run directory (parents=False, exist_ok=False to detect conflicts)
        try:
            run_ctx.run_directory.mkdir(parents=False, exist_ok=False)
            log_step(logger, "create_run_directory", "success", path=str(run_ctx.run_directory))
        except OSError as e:
            raise KasbenchError(
                f"Failed to create run directory '{run_ctx.run_directory}': {e}"
            ) from e

        # Create benchmark.db with schema
        db = DatabaseManager(run_ctx.db_path)
        db.create_schema()
        log_step(logger, "create_database", "success", path=str(run_ctx.db_path))

        # Verify database
        if not db.verify_schema():
            raise KasbenchError(
                f"Database verification failed: tables not found in '{run_ctx.db_path}'"
            )
        log_step(logger, "verify_database", "success", path=str(run_ctx.db_path))

        log_step(logger, "init_complete", "success")
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "init_failed", "failure", error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure", error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
