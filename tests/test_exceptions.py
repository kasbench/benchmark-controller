"""Tests for the kasbench_controller.exceptions module."""

import pytest

from kasbench_controller.exceptions import (
    DatabaseError,
    DuplicateTrialError,
    KasbenchError,
    RepositoryDownloadError,
    RunnerAPIError,
    S3UploadError,
    SSHError,
    TimeoutError,
    TofuError,
    ValidationError,
)


class TestExceptionHierarchy:
    """All custom exceptions inherit from KasbenchError."""

    @pytest.mark.parametrize(
        "exc_class",
        [
            DatabaseError,
            TofuError,
            RepositoryDownloadError,
            ValidationError,
            DuplicateTrialError,
            S3UploadError,
            SSHError,
            RunnerAPIError,
            TimeoutError,
        ],
    )
    def test_subclass_of_kasbench_error(self, exc_class: type) -> None:
        assert issubclass(exc_class, KasbenchError)

    @pytest.mark.parametrize(
        "exc_class",
        [
            DatabaseError,
            TofuError,
            RepositoryDownloadError,
            ValidationError,
            DuplicateTrialError,
            S3UploadError,
            SSHError,
            RunnerAPIError,
            TimeoutError,
        ],
    )
    def test_catchable_as_kasbench_error(self, exc_class: type) -> None:
        with pytest.raises(KasbenchError):
            if exc_class is TofuError:
                raise exc_class("msg", stdout="", stderr="", return_code=1)
            elif exc_class is RepositoryDownloadError:
                raise exc_class("msg", url="", status_code=None, elapsed=0.0)
            elif exc_class is S3UploadError:
                raise exc_class("msg", file_path="", stderr="")
            elif exc_class is SSHError:
                raise exc_class("msg", command="", stderr="", return_code=1)
            elif exc_class is RunnerAPIError:
                raise exc_class("msg", endpoint="", status_code=None, response_body="")
            elif exc_class is TimeoutError:
                raise exc_class("msg", operation="", elapsed=0.0)
            else:
                raise exc_class("msg")


class TestTofuError:
    """TofuError stores message, stdout, stderr, and return_code."""

    def test_stores_all_attributes(self) -> None:
        e = TofuError("apply failed", stdout="plan output", stderr="error detail", return_code=2)
        assert str(e) == "apply failed"
        assert e.stdout == "plan output"
        assert e.stderr == "error detail"
        assert e.return_code == 2

    def test_default_values(self) -> None:
        e = TofuError("msg")
        assert e.stdout == ""
        assert e.stderr == ""
        assert e.return_code == 1

    def test_partial_kwargs(self) -> None:
        e = TofuError("msg", stderr="oops")
        assert e.stdout == ""
        assert e.stderr == "oops"
        assert e.return_code == 1


class TestRepositoryDownloadError:
    """RepositoryDownloadError stores message, url, status_code, and elapsed."""

    def test_stores_all_attributes(self) -> None:
        e = RepositoryDownloadError(
            "download failed",
            url="https://github.com/kasbench/benchmark-infrastructure",
            status_code=404,
            elapsed=5.2,
        )
        assert str(e) == "download failed"
        assert e.url == "https://github.com/kasbench/benchmark-infrastructure"
        assert e.status_code == 404
        assert e.elapsed == 5.2

    def test_default_values(self) -> None:
        e = RepositoryDownloadError("timeout")
        assert e.url == ""
        assert e.status_code is None
        assert e.elapsed == 0.0

    def test_status_code_none(self) -> None:
        e = RepositoryDownloadError("network error", url="http://x", status_code=None, elapsed=1.0)
        assert e.status_code is None


class TestSimpleExceptions:
    """DatabaseError, ValidationError, DuplicateTrialError store message only."""

    def test_database_error_message(self) -> None:
        e = DatabaseError("cannot open db")
        assert str(e) == "cannot open db"

    def test_validation_error_message(self) -> None:
        e = ValidationError("invalid identifier")
        assert str(e) == "invalid identifier"

    def test_duplicate_trial_error_message(self) -> None:
        e = DuplicateTrialError("run1/trial1 already exists")
        assert str(e) == "run1/trial1 already exists"


class TestS3UploadError:
    """S3UploadError stores message, file_path, and stderr."""

    def test_stores_all_attributes(self) -> None:
        e = S3UploadError("upload failed", file_path="/tmp/file.json", stderr="access denied")
        assert str(e) == "upload failed"
        assert e.file_path == "/tmp/file.json"
        assert e.stderr == "access denied"

    def test_default_values(self) -> None:
        e = S3UploadError("msg")
        assert e.file_path == ""
        assert e.stderr == ""

    def test_partial_kwargs(self) -> None:
        e = S3UploadError("msg", file_path="/path/to/file")
        assert e.file_path == "/path/to/file"
        assert e.stderr == ""


class TestSSHError:
    """SSHError stores message, command, stderr, and return_code."""

    def test_stores_all_attributes(self) -> None:
        e = SSHError("ssh failed", command="docker pull x", stderr="connection refused", return_code=255)
        assert str(e) == "ssh failed"
        assert e.command == "docker pull x"
        assert e.stderr == "connection refused"
        assert e.return_code == 255

    def test_default_values(self) -> None:
        e = SSHError("msg")
        assert e.command == ""
        assert e.stderr == ""
        assert e.return_code == 1

    def test_partial_kwargs(self) -> None:
        e = SSHError("msg", command="ls", return_code=2)
        assert e.command == "ls"
        assert e.stderr == ""
        assert e.return_code == 2


class TestRunnerAPIError:
    """RunnerAPIError stores message, endpoint, status_code, and response_body."""

    def test_stores_all_attributes(self) -> None:
        e = RunnerAPIError(
            "request failed",
            endpoint="/initialize",
            status_code=500,
            response_body='{"error": "internal"}',
        )
        assert str(e) == "request failed"
        assert e.endpoint == "/initialize"
        assert e.status_code == 500
        assert e.response_body == '{"error": "internal"}'

    def test_default_values(self) -> None:
        e = RunnerAPIError("msg")
        assert e.endpoint == ""
        assert e.status_code is None
        assert e.response_body == ""

    def test_status_code_none(self) -> None:
        e = RunnerAPIError("connection error", endpoint="/status", status_code=None)
        assert e.status_code is None

    def test_partial_kwargs(self) -> None:
        e = RunnerAPIError("msg", endpoint="/start", status_code=503)
        assert e.endpoint == "/start"
        assert e.status_code == 503
        assert e.response_body == ""


class TestTimeoutError:
    """TimeoutError stores message, operation, and elapsed."""

    def test_stores_all_attributes(self) -> None:
        e = TimeoutError("timed out", operation="health_check", elapsed=30.5)
        assert str(e) == "timed out"
        assert e.operation == "health_check"
        assert e.elapsed == 30.5

    def test_default_values(self) -> None:
        e = TimeoutError("msg")
        assert e.operation == ""
        assert e.elapsed == 0.0

    def test_partial_kwargs(self) -> None:
        e = TimeoutError("msg", operation="rollout_wait")
        assert e.operation == "rollout_wait"
        assert e.elapsed == 0.0
