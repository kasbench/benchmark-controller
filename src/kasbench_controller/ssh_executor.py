"""SSH subprocess wrapper for KASBench Controller."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

from kasbench_controller.exceptions import SSHError
from kasbench_controller.logging import log_dry_run, log_step


@dataclass
class SSHResult:
    """Result of an SSH command execution."""

    return_code: int
    stdout: str
    stderr: str
    success: bool


class SSHExecutor:
    """Wrapper around SSH command execution via subprocess.run()."""

    def __init__(
        self,
        host: str,
        key_path: Path,
        user: str = "ubuntu",
        dry_run: bool = False,
    ) -> None:
        """Initialize the SSHExecutor.

        Args:
            host: The remote host IP or hostname to connect to.
            key_path: Path to the SSH private key file.
            user: The SSH user to connect as (default: "ubuntu").
            dry_run: If True, log planned commands without executing them.
        """
        self._host = host
        self._key_path = key_path
        self._user = user
        self._dry_run = dry_run
        self._logger: structlog.BoundLogger = structlog.get_logger()

    def execute(self, command: str, timeout: int = 120) -> SSHResult:
        """Execute a command on the remote host via SSH subprocess.

        Args:
            command: The command to execute on the remote host.
            timeout: Maximum time in seconds to wait for the command to complete
                (default: 120).

        Returns:
            SSHResult with the command outcome.

        Raises:
            SSHError: If the command exits with a non-zero return code.
        """
        ssh_cmd = [
            "ssh",
            "-i", str(self._key_path),
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            f"{self._user}@{self._host}",
            command,
        ]

        if self._dry_run:
            log_dry_run(
                self._logger,
                "ssh_execute",
                {
                    "host": self._host,
                    "user": self._user,
                    "key_path": str(self._key_path),
                    "command": command,
                    "timeout": timeout,
                },
            )
            return SSHResult(return_code=0, stdout="", stderr="", success=True)

        log_step(
            self._logger,
            "ssh_command",
            "success",
            host=self._host,
            user=self._user,
            command=command,
        )

        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        ssh_result = SSHResult(
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            success=result.returncode == 0,
        )

        if result.returncode != 0:
            log_step(
                self._logger,
                "ssh_command",
                "failure",
                host=self._host,
                command=command,
                return_code=result.returncode,
                stderr=result.stderr,
            )
            raise SSHError(
                message=f"SSH command failed on {self._host}: '{command}' exited with code {result.returncode}",
                command=command,
                stderr=result.stderr,
                return_code=result.returncode,
            )

        return ssh_result
