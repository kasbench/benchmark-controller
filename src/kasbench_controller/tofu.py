"""Open Tofu subprocess wrapper for KASBench Controller."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import structlog

from kasbench_controller.exceptions import TofuError
from kasbench_controller.logging import log_dry_run, log_step


@dataclass
class TofuResult:
    """Result of a tofu subprocess invocation."""

    return_code: int
    stdout: str
    stderr: str
    success: bool


class TofuRunner:
    """Wrapper around the tofu CLI binary using subprocess.run()."""

    def __init__(self, working_dir: Path, dry_run: bool = False) -> None:
        """Initialize the TofuRunner.

        Args:
            working_dir: The directory in which tofu commands will be executed
                (the Open_Tofu_Directory containing HCL files).
            dry_run: If True, log planned commands without executing them.
        """
        self._working_dir = working_dir
        self._dry_run = dry_run
        self._logger: structlog.BoundLogger = structlog.get_logger()

    def init(self) -> TofuResult:
        """Run `tofu init` in the working directory.

        Returns:
            TofuResult with the command outcome.

        Raises:
            TofuError: If the command exits with a non-zero return code.
        """
        if self._dry_run:
            log_dry_run(
                self._logger,
                "tofu init",
                {"cwd": str(self._working_dir)},
            )
            return TofuResult(return_code=0, stdout="", stderr="", success=True)

        return self._run(["tofu", "init"])

    def plan(
        self, var_files: list[str], variables: list[str], run_id: str
    ) -> TofuResult:
        """Run `tofu plan` with the specified variables.

        Args:
            var_files: List of var-file paths or filenames.
            variables: List of variable assignments (key=value).
            run_id: The run_id variable value (appended last).

        Returns:
            TofuResult with the command outcome.

        Raises:
            TofuError: If the command exits with a non-zero return code.
        """
        var_args = self._build_var_args(var_files, variables, run_id)
        cmd = ["tofu", "plan"] + var_args

        if self._dry_run:
            log_dry_run(
                self._logger,
                "tofu plan",
                {"cwd": str(self._working_dir), "args": var_args},
            )
            return TofuResult(return_code=0, stdout="", stderr="", success=True)

        return self._run(cmd)

    def apply(
        self,
        var_files: list[str],
        variables: list[str],
        run_id: str,
        auto_approve: bool,
    ) -> TofuResult:
        """Run `tofu apply` with the specified variables.

        Args:
            var_files: List of var-file paths or filenames.
            variables: List of variable assignments (key=value).
            run_id: The run_id variable value (appended last).
            auto_approve: If True, pass -auto-approve to tofu apply.

        Returns:
            TofuResult with the command outcome.

        Raises:
            TofuError: If the command exits with a non-zero return code.
        """
        var_args = self._build_var_args(var_files, variables, run_id)
        cmd = ["tofu", "apply"] + var_args

        if auto_approve:
            cmd.append("-auto-approve")

        if self._dry_run:
            log_dry_run(
                self._logger,
                "tofu apply",
                {
                    "cwd": str(self._working_dir),
                    "args": var_args,
                    "auto_approve": auto_approve,
                },
            )
            return TofuResult(return_code=0, stdout="", stderr="", success=True)

        return self._run(cmd)

    def output_json(self) -> dict:
        """Run `tofu output -json` and parse the JSON result.

        Returns:
            Parsed JSON dictionary of tofu outputs.

        Raises:
            TofuError: If the command exits with a non-zero return code or
                the output cannot be parsed as JSON.
        """
        if self._dry_run:
            log_dry_run(
                self._logger,
                "tofu output -json",
                {"cwd": str(self._working_dir)},
            )
            return {}

        result = self._run(["tofu", "output", "-json"])

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise TofuError(
                message=f"Failed to parse tofu output as JSON: {e}",
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.return_code,
            ) from e

    def _resolve_var_file(self, var_file: str) -> Path:
        """Resolve a var-file argument to an absolute path.

        If the var_file contains no path separator (os.sep or '/'), it is
        resolved relative to the `environments` subdirectory of the working
        directory. Otherwise, it is used as-is.

        Args:
            var_file: A var-file path or filename.

        Returns:
            The resolved Path.
        """
        if os.sep not in var_file and "/" not in var_file:
            return self._working_dir / "environments" / var_file
        return Path(var_file)

    def _build_var_args(
        self, var_files: list[str], variables: list[str], run_id: str
    ) -> list[str]:
        """Build the variable arguments list for tofu commands.

        Order: var-file arguments (in input order), then var arguments
        (in input order), then the run_id variable last.

        Args:
            var_files: List of var-file paths or filenames.
            variables: List of variable assignments (key=value).
            run_id: The run_id variable value.

        Returns:
            List of command-line arguments.
        """
        args: list[str] = []

        # Var-files first, in order
        for vf in var_files:
            resolved = self._resolve_var_file(vf)
            args.append(f"-var-file={resolved}")

        # Variables next, in order
        for var in variables:
            args.append(f"-var={var}")

        # run_id last
        args.append(f"-var=run_id={run_id}")

        return args

    def _run(self, args: list[str]) -> TofuResult:
        """Execute a tofu command via subprocess.run().

        Args:
            args: The full command and arguments to execute.

        Returns:
            TofuResult with the command outcome.

        Raises:
            TofuError: If the command exits with a non-zero return code.
        """
        log_step(
            self._logger,
            "tofu_command",
            "success",
            command=" ".join(args),
            cwd=str(self._working_dir),
        )

        result = subprocess.run(
            args,
            cwd=self._working_dir,
            capture_output=True,
            text=True,
        )

        tofu_result = TofuResult(
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            success=result.returncode == 0,
        )

        if result.returncode != 0:
            log_step(
                self._logger,
                "tofu_command",
                "failure",
                command=" ".join(args),
                return_code=result.returncode,
                stderr=result.stderr,
            )
            raise TofuError(
                message=f"Command '{' '.join(args)}' failed with exit code {result.returncode}",
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )

        return tofu_result
