"""Tests for the kasbench_controller.exceptions module."""

import pytest

from kasbench_controller.exceptions import (
    DatabaseError,
    DuplicateTrialError,
    KasbenchError,
    RepositoryDownloadError,
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
        ],
    )
    def test_catchable_as_kasbench_error(self, exc_class: type) -> None:
        with pytest.raises(KasbenchError):
            if exc_class is TofuError:
                raise exc_class("msg", stdout="", stderr="", return_code=1)
            elif exc_class is RepositoryDownloadError:
                raise exc_class("msg", url="", status_code=None, elapsed=0.0)
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
