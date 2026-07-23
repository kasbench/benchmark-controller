"""Parse Open Tofu JSON output to extract infrastructure details."""

from kasbench_controller.exceptions import ValidationError
from kasbench_controller.models import TofuOutputs


def parse_tofu_outputs(output: dict) -> TofuOutputs:
    """Parse infrastructure details from tofu output -json.

    Extracts benchmark_runner public IP, SSH key pair name, control plane
    private IP, worker node private IPs, and NLB DNS/port from the tofu
    output JSON.

    Args:
        output: Parsed JSON dictionary from `tofu output -json`.

    Returns:
        TofuOutputs dataclass with extracted values.

    Raises:
        ValidationError: If required keys are missing from the output.
    """
    missing_keys: list[str] = []

    # Extract benchmark_runner.public_ip
    public_ip: str | None = None
    try:
        value = output["benchmark_runner"]["value"]["public_ip"]
        if value == "<sensitive>":
            public_ip = None
        else:
            public_ip = value
    except (KeyError, TypeError):
        missing_keys.append("benchmark_runner.public_ip")

    # Extract ssh_key_pair_name
    key_pair_name: str | None = None
    try:
        value = output["ssh_key_pair_name"]["value"]
        if value == "<sensitive>":
            key_pair_name = None
        else:
            key_pair_name = value
    except (KeyError, TypeError):
        missing_keys.append("ssh_key_pair_name")

    # Extract control_plane.private_ip
    control_plane_private_ip: str | None = None
    try:
        value = output["control_plane"]["value"]["private_ip"]
        if value == "<sensitive>":
            control_plane_private_ip = None
        else:
            control_plane_private_ip = value
    except (KeyError, TypeError):
        missing_keys.append("control_plane.private_ip")

    # Extract worker_nodes.amd64 private IPs
    amd_worker_private_ips: list[str] = []
    try:
        amd_nodes = output["worker_nodes"]["value"]["amd64"]
        amd_worker_private_ips = [node["private_ip"] for node in amd_nodes]
    except (KeyError, TypeError):
        missing_keys.append("worker_nodes.amd64")

    # Extract worker_nodes.arm64 private IPs
    arm_worker_private_ips: list[str] = []
    try:
        arm_nodes = output["worker_nodes"]["value"]["arm64"]
        arm_worker_private_ips = [node["private_ip"] for node in arm_nodes]
    except (KeyError, TypeError):
        missing_keys.append("worker_nodes.arm64")

    # Extract nlb.dns_name
    globeco_dns: str | None = None
    try:
        value = output["nlb"]["value"]["dns_name"]
        if value == "<sensitive>":
            globeco_dns = None
        else:
            globeco_dns = value
    except (KeyError, TypeError):
        missing_keys.append("nlb.dns_name")

    # Extract nlb.listeners.http.port
    globeco_port: int | None = None
    try:
        value = output["nlb"]["value"]["listeners"]["http"]["port"]
        globeco_port = int(value)
    except (KeyError, TypeError):
        missing_keys.append("nlb.listeners.http.port")

    # Extract efs_file_system_id
    execution_data_fs: str | None = None
    try:
        value = output["efs_file_system_id"]["value"]
        if value == "<sensitive>":
            execution_data_fs = None
        else:
            execution_data_fs = value
    except (KeyError, TypeError):
        missing_keys.append("efs_file_system_id")

    if missing_keys:
        raise ValidationError(
            f"Missing required keys in tofu output: {', '.join(missing_keys)}"
        )

    return TofuOutputs(
        benchmark_runner_public_ip=public_ip,
        ssh_key_pair_name=key_pair_name,
        control_plane_private_ip=control_plane_private_ip,
        amd_worker_private_ips=amd_worker_private_ips,
        arm_worker_private_ips=arm_worker_private_ips,
        globeco_dns=globeco_dns,
        globeco_port=globeco_port,
        execution_data_fs=execution_data_fs,
        raw_json=output,
    )
