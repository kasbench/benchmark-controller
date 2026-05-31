"""Unit tests for the output_parser module."""

import pytest

from kasbench_controller.exceptions import ValidationError
from kasbench_controller.models import TofuOutputs
from kasbench_controller.output_parser import parse_tofu_outputs


class TestParseValidOutput:
    """Tests for successful parsing of valid tofu output JSON."""

    def test_extracts_public_ip_and_key_pair_name(self):
        output = {
            "benchmark_runner": {
                "value": {
                    "public_ip": "174.129.166.9",
                    "private_ip": "10.0.1.156",
                },
                "type": ["object", {}],
                "sensitive": False,
            },
            "ssh_key_pair_name": {
                "value": "kasbench-trial001",
                "type": "string",
                "sensitive": False,
            },
        }

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip == "174.129.166.9"
        assert result.ssh_key_pair_name == "kasbench-trial001"

    def test_returns_tofu_outputs_dataclass(self):
        output = {
            "benchmark_runner": {
                "value": {"public_ip": "10.0.0.1"},
            },
            "ssh_key_pair_name": {
                "value": "my-key",
            },
        }

        result = parse_tofu_outputs(output)

        assert isinstance(result, TofuOutputs)

    def test_preserves_raw_json(self):
        output = {
            "benchmark_runner": {
                "value": {"public_ip": "10.0.0.1"},
            },
            "ssh_key_pair_name": {
                "value": "my-key",
            },
            "extra_field": {"value": "extra"},
        }

        result = parse_tofu_outputs(output)

        assert result.raw_json is output

    def test_handles_additional_fields_in_benchmark_runner(self):
        output = {
            "benchmark_runner": {
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
            "ssh_key_pair_name": {
                "value": "kasbench-trial001",
                "type": "string",
                "sensitive": False,
            },
        }

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip == "52.1.2.3"
        assert result.ssh_key_pair_name == "kasbench-trial001"


class TestSensitiveMarkerHandling:
    """Tests for <sensitive> marker handling."""

    def test_sensitive_public_ip_returns_none(self):
        output = {
            "benchmark_runner": {
                "value": {"public_ip": "<sensitive>"},
            },
            "ssh_key_pair_name": {
                "value": "my-key",
            },
        }

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip is None
        assert result.ssh_key_pair_name == "my-key"

    def test_sensitive_key_pair_name_returns_none(self):
        output = {
            "benchmark_runner": {
                "value": {"public_ip": "10.0.0.1"},
            },
            "ssh_key_pair_name": {
                "value": "<sensitive>",
            },
        }

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip == "10.0.0.1"
        assert result.ssh_key_pair_name is None

    def test_both_sensitive_returns_none_for_both(self):
        output = {
            "benchmark_runner": {
                "value": {"public_ip": "<sensitive>"},
            },
            "ssh_key_pair_name": {
                "value": "<sensitive>",
            },
        }

        result = parse_tofu_outputs(output)

        assert result.benchmark_runner_public_ip is None
        assert result.ssh_key_pair_name is None

    def test_sensitive_does_not_raise_error(self):
        output = {
            "benchmark_runner": {
                "value": {"public_ip": "<sensitive>"},
            },
            "ssh_key_pair_name": {
                "value": "<sensitive>",
            },
        }

        # Should not raise
        parse_tofu_outputs(output)


class TestMissingKeys:
    """Tests for missing key error handling."""

    def test_missing_benchmark_runner_raises_validation_error(self):
        output = {
            "ssh_key_pair_name": {
                "value": "my-key",
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_missing_ssh_key_pair_name_raises_validation_error(self):
        output = {
            "benchmark_runner": {
                "value": {"public_ip": "10.0.0.1"},
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "ssh_key_pair_name" in str(exc_info.value)

    def test_missing_both_keys_lists_all_in_error(self):
        output = {}

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        error_msg = str(exc_info.value)
        assert "benchmark_runner.public_ip" in error_msg
        assert "ssh_key_pair_name" in error_msg

    def test_missing_value_key_in_benchmark_runner(self):
        output = {
            "benchmark_runner": {
                "type": ["object", {}],
            },
            "ssh_key_pair_name": {
                "value": "my-key",
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_missing_public_ip_in_value(self):
        output = {
            "benchmark_runner": {
                "value": {"private_ip": "10.0.1.1"},
            },
            "ssh_key_pair_name": {
                "value": "my-key",
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_benchmark_runner_value_is_none(self):
        output = {
            "benchmark_runner": {
                "value": None,
            },
            "ssh_key_pair_name": {
                "value": "my-key",
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_benchmark_runner_is_none(self):
        output = {
            "benchmark_runner": None,
            "ssh_key_pair_name": {
                "value": "my-key",
            },
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "benchmark_runner.public_ip" in str(exc_info.value)

    def test_ssh_key_pair_name_is_none(self):
        output = {
            "benchmark_runner": {
                "value": {"public_ip": "10.0.0.1"},
            },
            "ssh_key_pair_name": None,
        }

        with pytest.raises(ValidationError) as exc_info:
            parse_tofu_outputs(output)

        assert "ssh_key_pair_name" in str(exc_info.value)
