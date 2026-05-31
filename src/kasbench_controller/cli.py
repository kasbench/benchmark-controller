"""CLI entry point for KASBench Controller."""

import click

from kasbench_controller.commands import build_infrastructure, init
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
    ctx.obj["logger"] = configure_logging(log_file=log, dry_run=dry_run)


cli.add_command(init.init_cmd)
cli.add_command(build_infrastructure.build_infrastructure_cmd)
