"""Tests for the kasbench_controller.container_info module."""

from kasbench_controller.container_info import (
    ENV_IMAGE_ID,
    ENV_IMAGE_NAME,
    ENV_IMAGE_TAGS,
    ContainerImageInfo,
)


class TestFromEnv:
    """ContainerImageInfo.from_env reads and normalizes env vars."""

    def test_all_fields_populated(self) -> None:
        env = {
            ENV_IMAGE_NAME: "kasbench/kasbench-controller",
            ENV_IMAGE_TAGS: "latest,v1.0",
            ENV_IMAGE_ID: "sha256:abc123",
        }
        info = ContainerImageInfo.from_env(env)

        assert info.name == "kasbench/kasbench-controller"
        assert info.tags == ("latest", "v1.0")
        assert info.image_id == "sha256:abc123"
        assert info.is_available() is True

    def test_missing_vars_yield_empty_values(self) -> None:
        info = ContainerImageInfo.from_env({})

        assert info.name is None
        assert info.tags == ()
        assert info.image_id is None
        assert info.is_available() is False

    def test_blank_vars_treated_as_absent(self) -> None:
        env = {
            ENV_IMAGE_NAME: "   ",
            ENV_IMAGE_TAGS: "",
            ENV_IMAGE_ID: "\t",
        }
        info = ContainerImageInfo.from_env(env)

        assert info.name is None
        assert info.tags == ()
        assert info.image_id is None
        assert info.is_available() is False

    def test_tags_are_split_stripped_and_deblanked(self) -> None:
        env = {ENV_IMAGE_TAGS: " latest , , v1.0 ,"}
        info = ContainerImageInfo.from_env(env)

        assert info.tags == ("latest", "v1.0")

    def test_partial_provenance_is_available(self) -> None:
        info = ContainerImageInfo.from_env({ENV_IMAGE_ID: "sha256:deadbeef"})

        assert info.name is None
        assert info.tags == ()
        assert info.image_id == "sha256:deadbeef"
        assert info.is_available() is True


class TestAsDict:
    """as_dict produces a logging-friendly mapping."""

    def test_as_dict_shape(self) -> None:
        info = ContainerImageInfo(
            name="kasbench/kasbench-controller",
            tags=("latest", "v1.0"),
            image_id="sha256:abc123",
        )

        assert info.as_dict() == {
            "image_name": "kasbench/kasbench-controller",
            "image_tags": ["latest", "v1.0"],
            "image_id": "sha256:abc123",
        }

    def test_as_dict_when_empty(self) -> None:
        assert ContainerImageInfo().as_dict() == {
            "image_name": None,
            "image_tags": [],
            "image_id": None,
        }
