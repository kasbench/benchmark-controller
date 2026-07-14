"""Custom exception hierarchy for KASBench Controller."""


class KasbenchError(Exception):
    """Base exception for all KASBench Controller errors."""

    pass


class DatabaseError(KasbenchError):
    """Database operation failed."""

    pass


class TofuError(KasbenchError):
    """Open Tofu command failed."""

    def __init__(
        self,
        message: str,
        stdout: str = "",
        stderr: str = "",
        return_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.return_code = return_code


class RepositoryDownloadError(KasbenchError):
    """Repository download failed."""

    def __init__(
        self,
        message: str,
        url: str = "",
        status_code: int | None = None,
        elapsed: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.elapsed = elapsed


class ValidationError(KasbenchError):
    """Input validation failed."""

    pass


class DuplicateTrialError(KasbenchError):
    """Trial with same run_identifier and trial_identifier already exists."""

    pass


class S3UploadError(KasbenchError):
    """S3 upload operation failed."""

    def __init__(
        self,
        message: str,
        file_path: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.stderr = stderr


class SSHError(KasbenchError):
    """SSH command execution failed."""

    def __init__(
        self,
        message: str,
        command: str = "",
        stderr: str = "",
        return_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.stderr = stderr
        self.return_code = return_code


class RunnerAPIError(KasbenchError):
    """Runner API request failed."""

    def __init__(
        self,
        message: str,
        endpoint: str = "",
        status_code: int | None = None,
        response_body: str = "",
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.status_code = status_code
        self.response_body = response_body


class TimeoutError(KasbenchError):
    """Operation timed out."""

    def __init__(
        self,
        message: str,
        operation: str = "",
        elapsed: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.elapsed = elapsed
