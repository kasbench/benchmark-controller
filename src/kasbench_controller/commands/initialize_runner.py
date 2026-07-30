"""Initialize-runner subcommand - pulls runner image, starts container, and initializes the benchmark."""

import sys
import traceback
from pathlib import Path

import click

from kasbench_controller.exceptions import KasbenchError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.services.initialize_runner_service import run_initialize_runner


@click.command("initialize-runner")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--trial-identifier", required=True, type=str, help="Identifier for this trial")
@click.option("--runner-version", default="0.2.0", type=str, help="KASBench Runner Docker image version")
@click.option("--health-timeout", default=30, type=int, help="Health check polling timeout in seconds")
@click.option("--rollout-timeout", default=600, type=int, help="Rollout wait timeout in seconds")
@click.option("--cluster-cidr-range", default=None, type=str, help="Pod network CIDR to pass to the Runner (e.g. 10.244.0.0/16)")
@click.pass_context
def initialize_runner_cmd(
    ctx: click.Context,
    working_directory: str,
    run_identifier: str,
    trial_identifier: str,
    runner_version: str,
    health_timeout: int,
    rollout_timeout: int,
    cluster_cidr_range: str | None,
) -> None:
    """Initialize the KASBench Runner on the benchmark host."""
    logger = ctx.obj["logger"]
    dry_run = ctx.obj["dry_run"]

    try:
        # --- Dry-run mode ---
        if dry_run:
            log_dry_run(logger, "load_trial_config", {
                "config_path": str(Path(working_directory) / "benchmarks" / run_identifier / trial_identifier / "trial_config.json"),
            })
            log_dry_run(logger, "get_trial_by_identifiers", {
                "run_identifier": run_identifier,
                "trial_identifier": trial_identifier,
            })
            log_dry_run(logger, "docker_pull", {
                "image": f"kasbench/kasbench-runner:{runner_version}",
            })
            log_dry_run(logger, "docker_network_create", {
                "network": "kasbench",
            })
            log_dry_run(logger, "docker_run", {
                "container": "kasbench-runner",
                "network": "kasbench",
                "port": "8080:8080",
                "version": runner_version,
            })
            log_dry_run(logger, "health_check", {
                "timeout": health_timeout,
            })
            log_dry_run(logger, "initialize_runner", {
                "endpoint": "/initialize",
            })
            log_dry_run(logger, "rollout_wait", {
                "timeout": rollout_timeout,
            })
            log_dry_run(logger, "snapshot", {
                "phase": "pre",
            })
            log_step(logger, "initialize_runner_complete", "success", dry_run=True)
            sys.exit(0)

        # Delegate to the service function
        run_initialize_runner(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
            trial_identifier=trial_identifier,
            runner_version=runner_version,
            health_timeout=health_timeout,
            rollout_timeout=rollout_timeout,
            cluster_cidr_range=cluster_cidr_range,
            logger=logger,
        )
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "initialize_runner_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
