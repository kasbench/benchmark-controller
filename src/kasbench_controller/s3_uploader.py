"""S3 upload subprocess wrapper for KASBench Controller."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

from kasbench_controller.exceptions import S3UploadError
from kasbench_controller.logging import log_dry_run, log_step
from kasbench_controller.models import TrialContext


@dataclass
class S3UploadResult:
    """Result of an S3 upload operation."""

    success: bool
    source_path: str
    destination_uri: str
    stderr: str


class S3Uploader:
    """Wrapper around the AWS CLI for S3 uploads using subprocess.run()."""

    def __init__(self, bucket: str, region: str, dry_run: bool = False) -> None:
        """Initialize the S3Uploader.

        Args:
            bucket: The S3 bucket name (without s3:// prefix).
            region: The AWS region for the upload.
            dry_run: If True, log planned commands without executing them.
        """
        self._bucket = bucket
        self._region = region
        self._dry_run = dry_run
        self._logger: structlog.BoundLogger = structlog.get_logger()

    def upload_file(self, local_path: Path, s3_key: str) -> S3UploadResult:
        """Upload a single file to s3://{bucket}/{s3_key} using aws s3 cp.

        Args:
            local_path: Path to the local file to upload.
            s3_key: The S3 object key (path within the bucket).

        Returns:
            S3UploadResult with the operation outcome.

        Raises:
            S3UploadError: If the upload command fails.
        """
        destination_uri = f"s3://{self._bucket}/{s3_key}"

        if self._dry_run:
            log_dry_run(
                self._logger,
                "aws s3 cp",
                {
                    "source": str(local_path),
                    "destination": destination_uri,
                    "region": self._region,
                },
            )
            return S3UploadResult(
                success=True,
                source_path=str(local_path),
                destination_uri=destination_uri,
                stderr="",
            )

        cmd = [
            "aws", "s3", "cp",
            str(local_path),
            destination_uri,
            "--region", self._region,
        ]

        log_step(
            self._logger,
            "s3_upload",
            "success",
            command=" ".join(cmd),
            source=str(local_path),
            destination=destination_uri,
        )

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            log_step(
                self._logger,
                "s3_upload",
                "failure",
                source=str(local_path),
                destination=destination_uri,
                return_code=result.returncode,
                stderr=result.stderr,
            )
            raise S3UploadError(
                message=(
                    f"Failed to upload '{local_path}' to '{destination_uri}': "
                    f"exit code {result.returncode}"
                ),
                file_path=str(local_path),
                stderr=result.stderr,
            )

        return S3UploadResult(
            success=True,
            source_path=str(local_path),
            destination_uri=destination_uri,
            stderr=result.stderr,
        )

    def upload_trial_artifacts(
        self,
        trial_ctx: TrialContext,
        run_identifier: str,
        trial_identifier: str,
    ) -> list[S3UploadResult]:
        """Upload standard trial artifacts to S3.

        Uploads the following files:
        - tofu_outputs.json from the trial output directory
        - environment-description.json from the tofu artifacts directory
        - environment-description.md from the tofu artifacts directory

        Args:
            trial_ctx: The trial context providing directory paths.
            run_identifier: The run identifier for S3 path construction.
            trial_identifier: The trial identifier for S3 path construction.

        Returns:
            List of S3UploadResult for each uploaded file.

        Raises:
            S3UploadError: If any upload fails.
        """
        artifacts = [
            (
                trial_ctx.output_directory / "tofu_outputs.json",
                f"{run_identifier}/{trial_identifier}/infrastructure/tofu_outputs.json",
            ),
            (
                trial_ctx.tofu_directory / "artifacts" / trial_identifier / "environment-description.json",
                f"{run_identifier}/{trial_identifier}/infrastructure/environment-description.json",
            ),
            (
                trial_ctx.tofu_directory / "artifacts" / trial_identifier / "environment-description.md",
                f"{run_identifier}/{trial_identifier}/infrastructure/environment-description.md",
            ),
        ]

        results: list[S3UploadResult] = []
        for local_path, s3_key in artifacts:
            result = self.upload_file(local_path, s3_key)
            results.append(result)

        return results
