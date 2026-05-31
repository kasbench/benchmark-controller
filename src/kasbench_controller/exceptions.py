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
