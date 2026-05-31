"""Parse Open Tofu JSON output to extract infrastructure details."""

from kasbench_controller.exceptions import ValidationError
from kasbench_controller.models import TofuOutputs


def parse_tofu_outputs(output: dict) -> TofuOutputs:
    """Parse benchmark_runner.public_ip and ssh_key_pair_name from tofu output -json.

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

    if missing_keys:
        raise ValidationError(
            f"Missing required keys in tofu output: {', '.join(missing_keys)}"
        )

    return TofuOutputs(
        benchmark_runner_public_ip=public_ip,
        ssh_key_pair_name=key_pair_name,
        raw_json=output,
    )
