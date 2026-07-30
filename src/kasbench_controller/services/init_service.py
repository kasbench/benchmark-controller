"""Init service - core logic for initializing a new experimental run.

Extracted from commands/init.py so it can be called programmatically
by the experiment orchestrator without Click/sys.exit dependencies.
"""

import shutil
from pathlib import Path

import structlog

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_step
from kasbench_controller.models import RunContext


def run_init(
    working_directory: Path,
    run_identifier: str,
    logger: structlog.BoundLogger,
    force: bool = False,
) -> None:
    """Execute the init logic: create workspace, run directory, and database.

    Args:
        working_directory: Top-level working directory for the experiment.
        run_identifier: Identifier for this experimental run.
        logger: Structured logger instance.
        force: If True, overwrite an existing run directory.

    Raises:
        KasbenchError: On any failure (directory creation, DB init, schema verification).
    """
    run_ctx = RunContext(
        working_directory=working_directory,
        run_identifier=run_identifier,
    )

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
