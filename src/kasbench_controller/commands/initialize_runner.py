"""Initialize-runner subcommand - pulls runner image, starts container, and initializes the benchmark."""

import subprocess
import sys
import time
import traceback
from pathlib import Path

import click

from kasbench_controller.database import DatabaseManager
from kasbench_controller.exceptions import KasbenchError, SSHError, TimeoutError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import RunContext, TrialContext, load_trial_config
from kasbench_controller.runner_api import RunnerAPIClient
from kasbench_controller.ssh_executor import SSHExecutor


@click.command("initialize-runner")
@click.option("--working-directory", required=True, type=click.Path(), help="Top-level working directory")
@click.option("--run-identifier", required=True, type=str, help="Identifier for this experimental run")
@click.option("--trial-identifier", required=True, type=str, help="Identifier for this trial")
@click.option("--runner-version", default="0.2.0", type=str, help="KASBench Runner Docker image version")
@click.option("--health-timeout", default=30, type=int, help="Health check polling timeout in seconds")
@click.option("--rollout-timeout", default=600, type=int, help="Rollout wait timeout in seconds")
@click.pass_context
def initialize_runner_cmd(
    ctx: click.Context,
    working_directory: str,
    run_identifier: str,
    trial_identifier: str,
    runner_version: str,
    health_timeout: int,
    rollout_timeout: int,
) -> None:
    """Initialize the KASBench Runner on the benchmark host."""
    logger = ctx.obj["logger"]
    dry_run = ctx.obj["dry_run"]

    try:
        # Build context objects
        run_ctx = RunContext(
            working_directory=Path(working_directory),
            run_identifier=run_identifier,
        )
        # We need a TrialContext — autoscaler will be looked up from the database
        trial_ctx = TrialContext(
            run_context=run_ctx,
            trial_identifier=trial_identifier,
            autoscaler="",  # Placeholder; we'll get the real value from the DB
        )

        # --- Dry-run mode ---
        if dry_run:
            log_dry_run(logger, "load_trial_config", {
                "config_path": str(trial_ctx.output_directory / "trial_config.json"),
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

        # --- Step 1: Load trial config (prerequisite check) ---
        trial_config = load_trial_config(trial_ctx)
        log_step(logger, "load_trial_config", "success",
                 config_path=str(trial_ctx.output_directory / "trial_config.json"))

        # --- Step 2: Validate run directory and database ---
        if not run_ctx.db_path.exists():
            raise KasbenchError(
                f"Database file not found: '{run_ctx.db_path}'. "
                f"Run 'kasbench init' first."
            )

        db = DatabaseManager(run_ctx.db_path)

        # --- Step 3: Look up trial in database ---
        trial_record = db.get_trial_by_identifiers(run_identifier, trial_identifier)
        if trial_record is None:
            raise KasbenchError(
                f"No trial found with run_identifier='{run_identifier}' and "
                f"trial_identifier='{trial_identifier}'. "
                f"Has build-infrastructure been run?"
            )
        trial_id = trial_record["trial_id"]
        autoscaler = trial_record["autoscaler"]
        log_step(logger, "get_trial_by_identifiers", "success",
                 trial_id=trial_id, autoscaler=autoscaler)

        # --- Step 4: Set up SSH executor ---
        ssh_key_path = (
            trial_ctx.tofu_directory
            / "artifacts"
            / trial_identifier
            / "fleet_key.pem"
        )
        ssh = SSHExecutor(
            host=trial_config.benchmark_runner_public_ip,
            key_path=ssh_key_path,
            user="ubuntu",
            dry_run=False,
        )

        # --- Step 5: Docker pull ---
        pull_cmd = f"sudo docker pull kasbench/kasbench-runner:{runner_version}"
        ssh.execute(pull_cmd, timeout=300)
        log_step(logger, "docker_pull", "success",
                 image=f"kasbench/kasbench-runner:{runner_version}")
        db.insert_event(trial_id, "docker_pull", f"Pulled kasbench/kasbench-runner:{runner_version}")

        # --- Step 6: Docker network create (ignore "already exists" error) ---
        network_cmd = "sudo docker network create kasbench"
        try:
            ssh.execute(network_cmd)
            log_step(logger, "docker_network_create", "success", network="kasbench")
        except SSHError as e:
            if "already exists" in e.stderr:
                log_step(logger, "docker_network_create", "success",
                         network="kasbench", note="already exists")
            else:
                raise
        db.insert_event(trial_id, "docker_network_create", "Created docker network 'kasbench'")

        # --- Step 7: Prepare SSH key and run container ---
        # The runner container uses asyncssh to connect to cluster nodes. It looks
        # for keys at /root/.ssh/ (standard names like id_rsa). We need to place
        # the trial SSH private key on the runner host so it's available inside the
        # container when mounted.
        # First, copy the trial key to the runner host as ~/.ssh/id_rsa.
        scp_cmd = [
            "scp",
            "-i", str(ssh_key_path),
            "-o", "StrictHostKeyChecking=no",
            str(ssh_key_path),
            f"ubuntu@{trial_config.benchmark_runner_public_ip}:/home/ubuntu/.ssh/id_rsa",
        ]
        if not dry_run:
            scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=30)
            if scp_result.returncode != 0:
                raise KasbenchError(
                    f"Failed to copy SSH key to runner host: {scp_result.stderr}"
                )
        ssh.execute("chmod 400 /home/ubuntu/.ssh/id_rsa")
        log_step(logger, "prepare_ssh_key", "success",
                 source=str(ssh_key_path), dest="/home/ubuntu/.ssh/id_rsa")

        docker_run_cmd = (
            f"sudo docker run -d --name kasbench-runner --network kasbench "
            f"-p 8080:8080 "
            f"-v /var/run/docker.sock:/var/run/docker.sock "
            f"-v /home/ubuntu/.ssh:/root/.ssh:ro "
            f"kasbench/kasbench-runner:{runner_version}"
        )
        ssh.execute(docker_run_cmd)
        log_step(logger, "docker_run", "success", container="kasbench-runner")
        db.insert_event(trial_id, "docker_run", f"Started kasbench-runner container (v{runner_version})")

        # --- Step 8: Health check polling ---
        runner_api = RunnerAPIClient(
            base_url=f"http://{trial_config.benchmark_runner_public_ip}:8080",
            timeout=600.0, # 600 seconds, need to adjust
        )

        start_time = time.time()
        healthy = False
        while (time.time() - start_time) < health_timeout:
            if runner_api.health_check():
                healthy = True
                break
            time.sleep(1)

        if not healthy:
            raise TimeoutError(
                message=f"Health check timed out after {health_timeout}s. "
                f"Runner API at http://{trial_config.benchmark_runner_public_ip}:8080/status "
                f"did not return HTTP 200.",
                operation="health_check",
                elapsed=float(health_timeout),
            )
        log_step(logger, "health_check", "success",
                 elapsed=round(time.time() - start_time, 1))
        db.insert_event(trial_id, "health_check", "Runner API health check passed")

        # --- Step 9: Initialize runner ---
        initialize_body = {
            "autoscaler": autoscaler,
            "controlPlaneNode": trial_config.control_plane_private_ip,
            "amdWorkerNodes": trial_config.amd_worker_private_ips,
            "armWorkerNodes": trial_config.arm_worker_private_ips,
            "s3Bucket": trial_config.s3_bucket,
            "globecoUrl": f"http://{trial_config.globeco_dns}",
            "globecoPort": trial_config.globeco_port,
            "runIdentifier": run_identifier,
            "trialIdentifier": trial_identifier,
            "runDurationMinutes": trial_config.run_duration,
            "skipKubernetesInstall": False,
            "skipManifestInstall": False,
            "forceManifestInstall": True,
        }

        runner_api.initialize(initialize_body)
        log_step(logger, "initialize_runner", "success", endpoint="/initialize")
        db.insert_event(trial_id, "initialize", "Runner initialized successfully")

        # --- Step 10: Rollout wait ---
        # Create a dedicated client with a timeout long enough to let the runner
        # respond (the runner may block up to 300s waiting on the deployment).
        rollout_api = RunnerAPIClient(
            base_url=f"http://{trial_config.benchmark_runner_public_ip}:8080",
            timeout=360.0,
        )

        rollout_start = time.time()
        rollout_success = False
        while (time.time() - rollout_start) < rollout_timeout:
            try:
                response = rollout_api.rollout_wait(
                    deployment_name="globeco-confirmation-service",
                    namespace="globeco",
                    timeout=300,
                )
                if response.status_code == 200:
                    rollout_success = True
                    break
            except Exception:
                pass
            time.sleep(1)

        if not rollout_success:
            raise TimeoutError(
                message=f"Rollout wait timed out after {rollout_timeout}s. "
                f"Deployment 'globeco-confirmation-service' in namespace 'globeco' "
                f"did not become ready.",
                operation="rollout_wait",
                elapsed=float(rollout_timeout),
            )
        log_step(logger, "rollout_wait", "success",
                 elapsed=round(time.time() - rollout_start, 1))
        db.insert_event(trial_id, "rollout_wait", "Rollout wait completed successfully")

        # --- Step 11: Pre-benchmark snapshot ---
        runner_api.snapshot("pre")
        log_step(logger, "snapshot", "success", phase="pre")
        db.insert_event(trial_id, "snapshot", "Pre-benchmark snapshot taken")

        # --- Done ---
        log_step(logger, "initialize_runner_complete", "success")
        sys.exit(0)

    except KasbenchError as e:
        log_step(logger, "initialize_runner_failed", "failure",
                 error=str(e), context=e.__class__.__name__)
        sys.exit(1)
    except Exception as e:
        log_step(logger, "unexpected_error", "failure",
                 error=str(e), traceback=traceback.format_exc())
        sys.exit(1)
