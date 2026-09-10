"""CLI entry point for KASBench Controller."""

import click

from kasbench_controller.commands import (
    benchmark_monitor,
    benchmark_postprocessing,
    benchmark_start,
    build_infrastructure,
    destroy_infrastructure,
    init,
    initialize_runner,
    run_experiment,
    shutdown,
)
from kasbench_controller.container_info import ContainerImageInfo
from kasbench_controller.logging import configure_logging


@click.group()
@click.option("--log", type=click.Path(), default=None, help="Write structured logs to file")
@click.option("--dry-run", is_flag=True, default=False, help="Report operations without executing")
@click.pass_context
def cli(ctx: click.Context, log: str | None, dry_run: bool) -> None:
    """KASBench Controller - Kubernetes Autoscaling Benchmark Orchestrator."""
    ctx.ensure_object(dict)
    ctx.obj["log_file"] = log
    ctx.obj["dry_run"] = dry_run
    logger = configure_logging(log_file=log, dry_run=dry_run)

    # Image provenance is injected by the host at `docker run` time (see the
    # README "Image provenance" section). Read it once, stash it for commands
    # to interrogate, and record it so every run's logs capture which image ran.
    image_info = ContainerImageInfo.from_env()
    ctx.obj["image_info"] = image_info
    if image_info.is_available():
        logger.info("container_image", **image_info.as_dict())

    ctx.obj["logger"] = logger


cli.add_command(init.init_cmd)
cli.add_command(build_infrastructure.build_infrastructure_cmd)
cli.add_command(initialize_runner.initialize_runner_cmd)
cli.add_command(benchmark_start.benchmark_start_cmd)
cli.add_command(benchmark_monitor.benchmark_monitor_cmd)
cli.add_command(benchmark_postprocessing.benchmark_postprocessing_cmd)
cli.add_command(shutdown.shutdown_cmd)
cli.add_command(destroy_infrastructure.destroy_infrastructure_cmd)
cli.add_command(run_experiment.run_experiment_cmd)
