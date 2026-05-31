"""Tests for the repository download module."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
import structlog

from kasbench_controller.exceptions import RepositoryDownloadError
from kasbench_controller.repository import RepositoryDownloader


def _create_zipball(files: dict[str, bytes], top_level_dir: str = "benchmark-infrastructure-main") -> bytes:
    """Create a zip archive mimicking GitHub's zipball format.

    Args:
        files: Mapping of relative file paths to their content.
        top_level_dir: The top-level directory name in the archive.

    Returns:
        Raw bytes of the zip archive.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Add the top-level directory entry
        zf.writestr(f"{top_level_dir}/", "")
        for path, content in files.items():
            zf.writestr(f"{top_level_dir}/{path}", content)
    return buf.getvalue()


@pytest.fixture
def target_dir(tmp_path: Path) -> Path:
    """Provide a target directory for extraction."""
    return tmp_path / "benchmark-infrastructure"


@pytest.fixture
def logger() -> structlog.BoundLogger:
    """Provide a structlog logger for tests."""
    return structlog.get_logger()


@pytest.fixture
def sample_zipball() -> bytes:
    """Provide a sample zipball with typical repository contents."""
    return _create_zipball({
        "main.tf": b'resource "aws_instance" "example" {}',
        "variables.tf": b'variable "region" {}',
        "environments/dev.tfvars": b'region = "us-east-1"',
        ".kiro/config.json": b"{}",
        "requirements/req1.md": b"# Requirement 1",
        ".gitignore": b"*.tfstate",
    })


class TestRepositoryDownloaderInit:
    """Tests for RepositoryDownloader initialization."""

    def test_stores_target_dir(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        downloader = RepositoryDownloader(target_dir, logger=logger)
        assert downloader._target_dir == target_dir

    def test_dry_run_defaults_to_false(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        downloader = RepositoryDownloader(target_dir, logger=logger)
        assert downloader._dry_run is False

    def test_dry_run_can_be_set(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        downloader = RepositoryDownloader(target_dir, dry_run=True, logger=logger)
        assert downloader._dry_run is True


class TestDryRunMode:
    """Tests for dry-run mode behavior."""

    def test_does_not_create_target_dir(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        downloader = RepositoryDownloader(target_dir, dry_run=True, logger=logger)
        downloader.download_and_extract()
        assert not target_dir.exists()

    def test_does_not_make_http_requests(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        with respx.mock(assert_all_called=False) as router:
            router.get(RepositoryDownloader.ZIPBALL_URL).respond(200)
            downloader = RepositoryDownloader(target_dir, dry_run=True, logger=logger)
            downloader.download_and_extract()
            assert not router.calls


class TestDownloadAndExtract:
    """Tests for the download and extraction flow."""

    @respx.mock
    def test_successful_download_extracts_files(
        self, target_dir: Path, sample_zipball: bytes, logger: structlog.BoundLogger
    ) -> None:
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(200, content=sample_zipball)
        )
        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader.download_and_extract()

        assert (target_dir / "main.tf").exists()
        assert (target_dir / "variables.tf").exists()
        assert (target_dir / "environments" / "dev.tfvars").exists()

    @respx.mock
    def test_strips_top_level_directory(
        self, target_dir: Path, logger: structlog.BoundLogger
    ) -> None:
        zipball = _create_zipball({"file.txt": b"hello"}, top_level_dir="repo-main")
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(200, content=zipball)
        )
        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader.download_and_extract()

        # File should be directly in target_dir, not in target_dir/repo-main/
        assert (target_dir / "file.txt").exists()
        assert not (target_dir / "repo-main").exists()

    @respx.mock
    def test_creates_target_dir_if_not_exists(
        self, target_dir: Path, sample_zipball: bytes, logger: structlog.BoundLogger
    ) -> None:
        assert not target_dir.exists()
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(200, content=sample_zipball)
        )
        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader.download_and_extract()
        assert target_dir.exists()

    @respx.mock
    def test_cleanup_removes_unwanted_items(
        self, target_dir: Path, sample_zipball: bytes, logger: structlog.BoundLogger
    ) -> None:
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(200, content=sample_zipball)
        )
        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader.download_and_extract()

        assert not (target_dir / ".kiro").exists()
        assert not (target_dir / "requirements").exists()
        assert not (target_dir / ".gitignore").exists()
        assert not (target_dir / ".git").exists()


class TestRetryLogic:
    """Tests for retry behavior on transient errors."""

    @respx.mock
    def test_retries_on_5xx_status(
        self, target_dir: Path, sample_zipball: bytes, logger: structlog.BoundLogger
    ) -> None:
        route = respx.get(RepositoryDownloader.ZIPBALL_URL)
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(200, content=sample_zipball),
        ]
        with patch("kasbench_controller.repository.time.sleep") as mock_sleep:
            downloader = RepositoryDownloader(target_dir, logger=logger)
            downloader.download_and_extract()
            mock_sleep.assert_called_once_with(5)

        assert (target_dir / "main.tf").exists()

    @respx.mock
    def test_retries_on_connect_error(
        self, target_dir: Path, sample_zipball: bytes, logger: structlog.BoundLogger
    ) -> None:
        route = respx.get(RepositoryDownloader.ZIPBALL_URL)
        route.side_effect = [
            httpx.ConnectError("Connection refused"),
            httpx.Response(200, content=sample_zipball),
        ]
        with patch("kasbench_controller.repository.time.sleep") as mock_sleep:
            downloader = RepositoryDownloader(target_dir, logger=logger)
            downloader.download_and_extract()
            mock_sleep.assert_called_once_with(5)

    @respx.mock
    def test_retries_on_timeout(
        self, target_dir: Path, sample_zipball: bytes, logger: structlog.BoundLogger
    ) -> None:
        route = respx.get(RepositoryDownloader.ZIPBALL_URL)
        route.side_effect = [
            httpx.TimeoutException("Read timed out"),
            httpx.Response(200, content=sample_zipball),
        ]
        with patch("kasbench_controller.repository.time.sleep") as mock_sleep:
            downloader = RepositoryDownloader(target_dir, logger=logger)
            downloader.download_and_extract()
            mock_sleep.assert_called_once_with(5)

    @respx.mock
    def test_raises_after_max_retries_exhausted(
        self, target_dir: Path, logger: structlog.BoundLogger
    ) -> None:
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(503)
        )
        with patch("kasbench_controller.repository.time.sleep"):
            downloader = RepositoryDownloader(target_dir, logger=logger)
            with pytest.raises(RepositoryDownloadError) as exc_info:
                downloader.download_and_extract()

            assert exc_info.value.url == RepositoryDownloader.ZIPBALL_URL
            assert exc_info.value.status_code == 503

    @respx.mock
    def test_does_not_retry_on_4xx(
        self, target_dir: Path, logger: structlog.BoundLogger
    ) -> None:
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(404)
        )
        with patch("kasbench_controller.repository.time.sleep") as mock_sleep:
            downloader = RepositoryDownloader(target_dir, logger=logger)
            with pytest.raises(RepositoryDownloadError) as exc_info:
                downloader.download_and_extract()

            assert exc_info.value.status_code == 404
            mock_sleep.assert_not_called()

    @respx.mock
    def test_max_3_attempts(
        self, target_dir: Path, logger: structlog.BoundLogger
    ) -> None:
        route = respx.get(RepositoryDownloader.ZIPBALL_URL)
        route.side_effect = [
            httpx.Response(500),
            httpx.Response(502),
            httpx.Response(503),
        ]
        with patch("kasbench_controller.repository.time.sleep"):
            downloader = RepositoryDownloader(target_dir, logger=logger)
            with pytest.raises(RepositoryDownloadError):
                downloader.download_and_extract()

        assert route.call_count == 3


class TestErrorReporting:
    """Tests for error information in RepositoryDownloadError."""

    @respx.mock
    def test_includes_url_in_error(
        self, target_dir: Path, logger: structlog.BoundLogger
    ) -> None:
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(404)
        )
        downloader = RepositoryDownloader(target_dir, logger=logger)
        with pytest.raises(RepositoryDownloadError) as exc_info:
            downloader.download_and_extract()

        assert exc_info.value.url == RepositoryDownloader.ZIPBALL_URL

    @respx.mock
    def test_includes_status_code_in_error(
        self, target_dir: Path, logger: structlog.BoundLogger
    ) -> None:
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(403)
        )
        downloader = RepositoryDownloader(target_dir, logger=logger)
        with pytest.raises(RepositoryDownloadError) as exc_info:
            downloader.download_and_extract()

        assert exc_info.value.status_code == 403

    @respx.mock
    def test_includes_elapsed_time_in_error(
        self, target_dir: Path, logger: structlog.BoundLogger
    ) -> None:
        respx.get(RepositoryDownloader.ZIPBALL_URL).mock(
            return_value=httpx.Response(404)
        )
        downloader = RepositoryDownloader(target_dir, logger=logger)
        with pytest.raises(RepositoryDownloadError) as exc_info:
            downloader.download_and_extract()

        assert exc_info.value.elapsed >= 0.0


class TestCleanupUnwantedFiles:
    """Tests for the cleanup of unwanted files after extraction."""

    def test_removes_kiro_directory(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        target_dir.mkdir(parents=True)
        (target_dir / ".kiro").mkdir()
        (target_dir / ".kiro" / "config.json").write_text("{}")

        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader._cleanup_unwanted_files()

        assert not (target_dir / ".kiro").exists()

    def test_removes_requirements_directory(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        target_dir.mkdir(parents=True)
        (target_dir / "requirements").mkdir()
        (target_dir / "requirements" / "req.md").write_text("# Req")

        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader._cleanup_unwanted_files()

        assert not (target_dir / "requirements").exists()

    def test_removes_gitignore_file(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        target_dir.mkdir(parents=True)
        (target_dir / ".gitignore").write_text("*.tfstate")

        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader._cleanup_unwanted_files()

        assert not (target_dir / ".gitignore").exists()

    def test_removes_git_directory(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        target_dir.mkdir(parents=True)
        (target_dir / ".git").mkdir()
        (target_dir / ".git" / "HEAD").write_text("ref: refs/heads/main")

        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader._cleanup_unwanted_files()

        assert not (target_dir / ".git").exists()

    def test_skips_missing_items_silently(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        target_dir.mkdir(parents=True)
        # No cleanup items exist - should not raise
        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader._cleanup_unwanted_files()

    def test_preserves_other_files(self, target_dir: Path, logger: structlog.BoundLogger) -> None:
        target_dir.mkdir(parents=True)
        (target_dir / "main.tf").write_text('resource "aws_instance" {}')
        (target_dir / ".gitignore").write_text("*.tfstate")

        downloader = RepositoryDownloader(target_dir, logger=logger)
        downloader._cleanup_unwanted_files()

        assert (target_dir / "main.tf").exists()
        assert not (target_dir / ".gitignore").exists()


class TestHttpTimeout:
    """Tests for HTTP timeout configuration."""

    @respx.mock
    def test_uses_120_second_timeout(
        self, target_dir: Path, sample_zipball: bytes, logger: structlog.BoundLogger
    ) -> None:
        """Verify the timeout is set to 120 seconds."""
        assert RepositoryDownloader.TIMEOUT_SECONDS == 120
