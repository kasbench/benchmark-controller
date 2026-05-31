"""GitHub repository download and extraction for KASBench Controller."""

from __future__ import annotations

import shutil
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import structlog

from kasbench_controller.exceptions import RepositoryDownloadError
from kasbench_controller.logging import log_dry_run, log_step


class RepositoryDownloader:
    """Downloads and extracts the benchmark-infrastructure repository from GitHub."""

    REPO_URL = "https://github.com/kasbench/benchmark-infrastructure"
    ZIPBALL_URL = "https://github.com/kasbench/benchmark-infrastructure/archive/refs/heads/main.zip"
    TIMEOUT_SECONDS = 120
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 5

    CLEANUP_ITEMS = [".kiro", "requirements", ".gitignore", ".git"]

    def __init__(
        self,
        target_dir: Path,
        dry_run: bool = False,
        logger: structlog.BoundLogger | None = None,
    ) -> None:
        self._target_dir = target_dir
        self._dry_run = dry_run
        self._logger = logger or structlog.get_logger()

    def download_and_extract(self) -> None:
        """Download the repository zipball and extract contents to target_dir.

        In dry-run mode, logs what would happen and returns without downloading.

        Raises:
            RepositoryDownloadError: If the download fails after all retry attempts.
        """
        if self._dry_run:
            log_dry_run(
                self._logger,
                "download_repository",
                {
                    "url": self.ZIPBALL_URL,
                    "target_dir": str(self._target_dir),
                    "timeout_seconds": self.TIMEOUT_SECONDS,
                },
            )
            return

        self._target_dir.mkdir(parents=True, exist_ok=True)

        content = self._download_with_retry()
        self._extract_zip(content)
        self._cleanup_unwanted_files()

        log_step(
            self._logger,
            "download_repository",
            "success",
            url=self.ZIPBALL_URL,
            target_dir=str(self._target_dir),
        )

    def _download_with_retry(self) -> bytes:
        """Download the zipball with retry logic for transient errors.

        Returns:
            The raw bytes of the downloaded zip archive.

        Raises:
            RepositoryDownloadError: If all retry attempts are exhausted or a
                non-retryable error occurs.
        """
        last_exception: Exception | None = None
        last_status_code: int | None = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            start_time = time.time()
            try:
                with httpx.Client(timeout=self.TIMEOUT_SECONDS) as client:
                    response = client.get(self.ZIPBALL_URL, follow_redirects=True)

                elapsed = time.time() - start_time

                if response.status_code == 200:
                    return response.content

                # Non-retryable client errors (4xx)
                if 400 <= response.status_code < 500:
                    raise RepositoryDownloadError(
                        f"Repository download failed with status {response.status_code}",
                        url=self.ZIPBALL_URL,
                        status_code=response.status_code,
                        elapsed=elapsed,
                    )

                # Retryable server errors (5xx)
                last_status_code = response.status_code
                last_exception = None
                log_step(
                    self._logger,
                    "download_repository",
                    "failure",
                    attempt=attempt,
                    status_code=response.status_code,
                    elapsed=elapsed,
                    retrying=attempt < self.MAX_RETRIES,
                )

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                elapsed = time.time() - start_time
                last_exception = exc
                last_status_code = None
                log_step(
                    self._logger,
                    "download_repository",
                    "failure",
                    attempt=attempt,
                    error=str(exc),
                    elapsed=elapsed,
                    retrying=attempt < self.MAX_RETRIES,
                )

            except RepositoryDownloadError:
                raise

            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY_SECONDS)

        # All retries exhausted
        elapsed = time.time() - start_time
        if last_exception is not None:
            raise RepositoryDownloadError(
                f"Repository download failed after {self.MAX_RETRIES} attempts: {last_exception}",
                url=self.ZIPBALL_URL,
                status_code=last_status_code,
                elapsed=elapsed,
            )
        else:
            raise RepositoryDownloadError(
                f"Repository download failed after {self.MAX_RETRIES} attempts with status {last_status_code}",
                url=self.ZIPBALL_URL,
                status_code=last_status_code,
                elapsed=elapsed,
            )

    def _extract_zip(self, content: bytes) -> None:
        """Extract zip contents, stripping the top-level directory prefix.

        GitHub zipball archives contain a single top-level directory
        (e.g., `benchmark-infrastructure-main/`). This method strips that
        prefix so files land directly in target_dir.
        """
        with zipfile.ZipFile(BytesIO(content)) as zf:
            # Find the common top-level directory prefix
            names = zf.namelist()
            if not names:
                return

            # The top-level directory is the first path component of any entry
            top_level_prefix = names[0].split("/")[0] + "/"

            for member in zf.infolist():
                # Skip the top-level directory entry itself
                if member.filename == top_level_prefix:
                    continue

                # Strip the top-level prefix
                relative_path = member.filename[len(top_level_prefix) :]
                if not relative_path:
                    continue

                target_path = self._target_dir / relative_path

                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, open(
                        target_path, "wb"
                    ) as dest:
                        dest.write(source.read())

    def _cleanup_unwanted_files(self) -> None:
        """Remove unwanted files and directories from the extracted repository.

        Removes .kiro, requirements, .gitignore, and .git from target_dir.
        Silently skips items that do not exist.
        """
        for item_name in self.CLEANUP_ITEMS:
            item_path = self._target_dir / item_name
            if not item_path.exists():
                continue

            if item_path.is_dir():
                shutil.rmtree(item_path)
            else:
                item_path.unlink()
