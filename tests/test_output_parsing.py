"""Unit tests for the output_parser module."""

import pytest

from kasbench_controller.exceptions import ValidationError
from kasbench_controller.models import TofuOutputs
from kasbench_controller.output_parser import parse_tofu_outputs


def _make_valid_output(**overrides) -> dict:
    """Build a complete valid tofu output dict, with optional overrides."""
    output = {
        "benchmark_runner": {
            "value": {"public_ip": "174.129.166.9"},
        },
        "ssh_key_pair_name": {
            "value": "kasbench-trial001",
        },
        "control_plane": {
            "value": {"private_ip": "10.0.2.206"},
        },
        "worker_nodes": {
            "value": {
                "amd64": [{"private_ip": "10.0.2.116"}],
                "arm64": [{"private_ip": "10.0.2.4"}],
            },
        },
        "nlb": {
            "value": {
                "dns_name": "kasb-xxx.elb.us-east-1.amazonaws.com",
                "listeners": {"http": {"port": 80}},
            },
        },
    }
    output.update(overrides)
    return output


class TestParseValidOutput:
    """Tests for successful parsing of valid tofu output JSON."""

    def test_extracts_public_ip_and_key_pair_name(self):
        output = _make_valid_output(
            benchmark_runner={
                "value": {
                    "public_ip": "174.129.166.9",
                    "private_ip": "10.0.1.156",
                },
                "type": ["object", {}],
                "sensitive": False,
            },
            ssh_key_pair_name={
                "value": "kasbench-trial001",
                "type": "string",
                "sensitive": False,
            },
        )

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip == "174.129.166.9"
        assert result.ssh_key_pair_name == "kasbench-trial001"

    def test_returns_tofu_outputs_dataclass(self):
        output = _make_valid_output()

        result = parse_tofu_outputs(output)

        assert isinstance(result, TofuOutputs)

    def test_preserves_raw_json(self):
        output = _make_valid_output(extra_field={"value": "extra"})

        result = parse_tofu_outputs(output)

        assert result.raw_json is output

    def test_handles_additional_fields_in_benchmark_runner(self):
        output = _make_valid_output(
            benchmark_runner={
                "value": {
                    "ami_id": "ami-123",
                    "architecture": "amd64",
                    "instance_id": "i-abc",
                    "instance_type": "t3.small",
                    "private_ip": "10.0.1.156",
                    "public_ip": "52.1.2.3",
                    "root_volume_id": "vol-xyz",
                    "subnet_id": "subnet-abc",
                },
                "type": ["object", {}],
                "sensitive": False,
            },
        )

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip == "52.1.2.3"
        assert result.ssh_key_pair_name == "kasbench-trial001"

    def test_extracts_control_plane_private_ip(self):
        output = _make_valid_output()

        result = parse_tofu_outputs(output)

        assert result.control_plane_private_ip == "10.0.2.206"

    def test_extracts_amd_worker_private_ips(self):
        output = _make_valid_output(
            worker_nodes={
                "value": {
                    "amd64": [
                        {"private_ip": "10.0.2.100"},
                        {"private_ip": "10.0.2.101"},
                        {"private_ip": "10.0.2.102"},
                    ],
                    "arm64": [{"private_ip": "10.0.2.4"}],
                },
            },
        )

        result = parse_tofu_outputs(output)

        assert result.amd_worker_private_ips == [
            "10.0.2.100",
            "10.0.2.101",
            "10.0.2.102",
        ]

    def test_extracts_arm_worker_private_ips(self):
        output = _make_valid_output(
            worker_nodes={
                "value": {
                    "amd64": [{"private_ip": "10.0.2.116"}],
                    "arm64": [
                        {"private_ip": "10.0.2.50"},
                        {"private_ip": "10.0.2.51"},
                    ],
                },
            },
        )

        result = parse_tofu_outputs(output)

        assert result.arm_worker_private_ips == ["10.0.2.50", "10.0.2.51"]

    def test_preserves_worker_ip_order(self):
        ips = ["10.0.2.9", "10.0.2.1", "10.0.2.5", "10.0.2.3"]
        output = _make_valid_output(
            worker_nodes={
                "value": {
                    "amd64": [{"private_ip": ip} for ip in ips],
                    "arm64": [{"private_ip": "10.0.2.4"}],
                },
            },
        )

        result = parse_tofu_outputs(output)

        assert result.amd_worker_private_ips == ips

    def test_extracts_globeco_dns(self):
        output = _make_valid_output()

        result = parse_tofu_outputs(output)

        assert result.globeco_dns == "kasb-xxx.elb.us-east-1.amazonaws.com"

    def test_extracts_globeco_port_as_integer(self):
        output = _make_valid_output()

        result = parse_tofu_outputs(output)

        assert result.globeco_port == 80
        assert isinstance(result.globeco_port, int)

    def test_globeco_port_coerced_from_string(self):
        output = _make_valid_output(
            nlb={
                "value": {
                    "dns_name": "test.elb.amazonaws.com",
                    "listeners": {"http": {"port": "8080"}},
                },
            },
        )

        result = parse_tofu_outputs(output)

        assert result.globeco_port == 8080
        assert isinstance(result.globeco_port, int)

    def test_empty_worker_lists_are_valid(self):
        output = _make_valid_output(
            worker_nodes={
                "value": {
                    "amd64": [],
                    "arm64": [],
                },
            },
        )

        result = parse_tofu_outputs(output)

        assert result.amd_worker_private_ips == []
        assert result.arm_worker_private_ips == []


class TestSensitiveMarkerHandling:
    """Tests for <sensitive> marker handling."""

    def test_sensitive_public_ip_returns_none(self):
        output = _make_valid_output(
            benchmark_runner={"value": {"public_ip": "<sensitive>"}},
        )

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip is None
        assert result.ssh_key_pair_name == "kasbench-trial001"

    def test_sensitive_key_pair_name_returns_none(self):
        output = _make_valid_output(
            ssh_key_pair_name={"value": "<sensitive>"},
        )

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip == "174.129.166.9"
        assert result.ssh_key_pair_name is None

    def test_both_sensitive_returns_none_for_both(self):
        output = _make_valid_output(
            benchmark_runner={"value": {"public_ip": "<sensitive>"}},
            ssh_key_pair_name={"value": "<sensitive>"},
        )

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip is None
        assert result.ssh_key_pair_name is None

    def test_sensitive_does_not_raise_error(self):
        output = _make_valid_output(
            benchmark_runner={"value": {"public_ip": "<sensitive>"}},
            ssh_key_pair_name={"value": "<sensitive>"},
        )

        # Should not raise
        parse_tofu_outputs(output)

    def test_sensitive_control_plane_ip_returns_none(self):
        output = _make_valid_output(
            control_plane={"value": {"private_ip": "<sensitive>"}},
        )

        result = parse_tofu_outputs(output)

        assert result.control_plane_private_ip is None

    def test_sensitive_globeco_dns_returns_none(self):
        output = _make_valid_output(
            nlb={
                "value": {
                    "dns_name": "<sensitive>",
                    "listeners": {"http": {"port": 80}},
                },
            },
        )

        result = parse_tofu_outputs(output)

        assert result.globeco_dns is None


class TestMissingKeys:
    """Tests for missing key error handling."""

    def test_missing_benchmark_runner_raises_validation_error(self):
        output = _make_valid_output()
        del output["benchmark_runner"]

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_missing_ssh_key_pair_name_raises_validation_error(self):
        output = _make_valid_output()
        del output["ssh_key_pair_name"]

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "ssh_key_pair_name" in str(exc_info.value)

    def test_missing_all_keys_lists_all_in_error(self):
        output = {}

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        error_msg = str(exc_info.value)
        assert "benchmark_runner.public_ip" in error_msg
        assert "ssh_key_pair_name" in error_msg
        assert "control_plane.private_ip" in error_msg
        assert "worker_nodes.amd64" in error_msg
        assert "worker_nodes.arm64" in error_msg
        assert "nlb.dns_name" in error_msg
        assert "nlb.listeners.http.port" in error_msg

    def test_missing_value_key_in_benchmark_runner(self):
        output = _make_valid_output(
            benchmark_runner={"type": ["object", {}]},
        )

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_missing_public_ip_in_value(self):
        output = _make_valid_output(
            benchmark_runner={"value": {"private_ip": "10.0.1.1"}},
        )

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_benchmark_runner_value_is_none(self):
        output = _make_valid_output(
            benchmark_runner={"value": None},
        )

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_benchmark_runner_is_none(self):
        output = _make_valid_output(
            benchmark_runner=None,
        )

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_ssh_key_pair_name_is_none(self):
        output = _make_valid_output(
            ssh_key_pair_name=None,
        )

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "ssh_key_pair_name" in str(exc_info.value)

    def test_missing_control_plane_raises_validation_error(self):
        output = _make_valid_output()
        del output["control_plane"]

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "control_plane.private_ip" in str(exc_info.value)

    def test_missing_worker_nodes_raises_validation_error(self):
        output = _make_valid_output()
        del output["worker_nodes"]

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        error_msg = str(exc_info.value)
        assert "worker_nodes.amd64" in error_msg
        assert "worker_nodes.arm64" in error_msg

    def test_missing_nlb_raises_validation_error(self):
        output = _make_valid_output()
        del output["nlb"]

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        error_msg = str(exc_info.value)
        assert "nlb.dns_name" in error_msg
        assert "nlb.listeners.http.port" in error_msg

    def test_missing_nlb_listeners_raises_validation_error(self):
        output = _make_valid_output(
            nlb={"value": {"dns_name": "test.elb.amazonaws.com"}},
        )

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "nlb.listeners.http.port" in str(exc_info.value)

    def test_aggregates_multiple_missing_keys(self):
        """Verifies all missing keys are reported in a single error."""
        output = _make_valid_output()
        del output["control_plane"]
        del output["nlb"]

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        error_msg = str(exc_info.value)
        assert "control_plane.private_ip" in error_msg
        assert "nlb.dns_name" in error_msg
        assert "nlb.listeners.http.port" in error_msg
