"""Structured logging setup for KASBench Controller using structlog with JSON Lines output."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog


def _add_timestamp(
    logger: logging.Logger, method_name: str, event_dict: dict
) -> dict:
    """Add ISO 8601 UTC timestamp to every log entry."""
    event_dict["timestamp"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return event_dict


def configure_logging(
    log_file: Path | None = None, dry_run: bool = False
) -> structlog.BoundLogger:
    """Configure structlog for JSON Lines output to stdout and optionally a file.

    Args:
        log_file: Optional path to write JSON Lines log output to (in addition to stdout).
        dry_run: Whether the controller is running in dry-run mode.

    Returns:
        A configured structlog BoundLogger instance.

    Raises:
        SystemExit: If log_file is provided but cannot be created or written to.
    """
    # Set up stdlib logging handlers
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file is not None:
        try:
            log_file = Path(log_file)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(str(log_file), mode="a")
            handlers.append(file_handler)
        except OSError as e:
            print(
                f"Error: Cannot create or write to log file '{log_file}': {e}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Configure stdlib logging as the output backend
    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
        level=logging.INFO,
        force=True,
    )

    # Configure structlog processors
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            _add_timestamp,
            structlog.stdlib.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    logger = structlog.get_logger()

    if dry_run:
        logger = logger.bind(dry_run=True)

    return logger


def log_step(
    logger: structlog.BoundLogger, step: str, outcome: str, **kwargs
) -> None:
    """Emit a structured log entry for an operational step.

    Args:
        logger: The structlog BoundLogger instance.
        step: Description of the operational step (e.g., "create_run_directory").
        outcome: Result of the step ("success" or "failure").
        **kwargs: Additional context fields to include in the log entry.
    """
    log_method = logger.error if outcome == "failure" else logger.info
    log_method("step", step=step, outcome=outcome, **kwargs)


def log_dry_run(
    logger: structlog.BoundLogger, operation: str, details: dict
) -> None:
    """Emit a dry-run log entry describing what would be performed.

    Args:
        logger: The structlog BoundLogger instance.
        operation: Description of the operation that would be performed.
        details: Dictionary of details about the planned operation.
    """
    logger.info("dry_run", operation=operation, **details)
