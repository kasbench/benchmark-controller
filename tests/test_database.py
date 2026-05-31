"""Unit tests for the database module."""

import sqlite3
from datetime import datetime, timezone

import pytest

from kasbench_controller.database import DatabaseManager, VALID_STATUSES
from kasbench_controller.exceptions import DatabaseError, DuplicateTrialError


class TestDatabaseManagerInit:
    """Tests for DatabaseManager initialization."""

    def test_creates_connection_with_foreign_keys(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        cursor = manager._conn.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1

    def test_raises_database_error_on_invalid_path(self, tmp_path):
        # A directory path cannot be opened as a database file on most systems,
        # but sqlite3 may create it. Use a path inside a non-existent dir.
        bad_path = tmp_path / "nonexistent_dir" / "subdir" / "test.db"
        # sqlite3 will actually create intermediate files, so we need a truly
        # invalid scenario. Let's use a path where the parent is a file.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        invalid_path = blocker / "test.db"
        with pytest.raises(DatabaseError):
            DatabaseManager(invalid_path)


class TestCreateSchema:
    """Tests for schema creation."""

    def test_creates_trials_table(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager.create_schema()

        cursor = manager._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trials'"
        )
        assert cursor.fetchone() is not None

    def test_creates_events_table(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager.create_schema()

        cursor = manager._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        )
        assert cursor.fetchone() is not None

    def test_trials_status_check_constraint_accepts_valid(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager.create_schema()

        for status in VALID_STATUSES:
            manager._conn.execute(
                "INSERT INTO trials (status, autoscaler) VALUES (?, ?)",
                (status, "test-autoscaler"),
            )
        manager._conn.commit()

        cursor = manager._conn.execute("SELECT COUNT(*) FROM trials")
        assert cursor.fetchone()[0] == len(VALID_STATUSES)

    def test_trials_status_check_constraint_rejects_invalid(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager.create_schema()

        with pytest.raises(sqlite3.IntegrityError):
            manager._conn.execute(
                "INSERT INTO trials (status, autoscaler) VALUES (?, ?)",
                ("INVALID_STATUS", "test-autoscaler"),
            )

    def test_events_foreign_key_enforced(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager.create_schema()

        # Inserting an event with a non-existent trial_id should fail
        with pytest.raises(sqlite3.IntegrityError):
            manager._conn.execute(
                "INSERT INTO events (trial_id, event_type) VALUES (?, ?)",
                (9999, "TEST_EVENT"),
            )

    def test_events_foreign_key_accepts_valid_trial(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager.create_schema()

        # Insert a trial first
        manager._conn.execute(
            "INSERT INTO trials (autoscaler) VALUES (?)", ("karpenter",)
        )
        manager._conn.commit()

        # Now insert an event referencing that trial
        manager._conn.execute(
            "INSERT INTO events (trial_id, event_type) VALUES (?, ?)",
            (1, "INFRA_START"),
        )
        manager._conn.commit()

        cursor = manager._conn.execute("SELECT COUNT(*) FROM events")
        assert cursor.fetchone()[0] == 1

    def test_create_schema_is_idempotent(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager.create_schema()
        # Calling again should not raise
        manager.create_schema()


class TestVerifySchema:
    """Tests for schema verification."""

    def test_returns_true_when_both_tables_exist(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager.create_schema()
        assert manager.verify_schema() is True

    def test_returns_false_when_no_tables(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        assert manager.verify_schema() is False

    def test_returns_false_when_only_trials_exists(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager._conn.execute(
            "CREATE TABLE trials (trial_id INTEGER PRIMARY KEY)"
        )
        assert manager.verify_schema() is False

    def test_returns_false_when_only_events_exists(self, tmp_path):
        db_path = tmp_path / "test.db"
        manager = DatabaseManager(db_path)
        manager._conn.execute(
            "CREATE TABLE events (event_id INTEGER PRIMARY KEY)"
        )
        assert manager.verify_schema() is False


@pytest.fixture
def db_manager(tmp_path):
    """Provides a DatabaseManager with schema already created."""
    db_path = tmp_path / "test.db"
    manager = DatabaseManager(db_path)
    manager.create_schema()
    return manager


class TestInsertTrial:
    """Tests for insert_trial."""

    def test_returns_generated_trial_id(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        assert isinstance(trial_id, int)
        assert trial_id >= 1

    def test_inserts_with_status_pending(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        cursor = db_manager._conn.execute(
            "SELECT status FROM trials WHERE trial_id = ?", (trial_id,)
        )
        assert cursor.fetchone()[0] == "PENDING"

    def test_preserves_run_identifier(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        cursor = db_manager._conn.execute(
            "SELECT run_identifier FROM trials WHERE trial_id = ?", (trial_id,)
        )
        assert cursor.fetchone()[0] == "run001"

    def test_preserves_trial_identifier(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        cursor = db_manager._conn.execute(
            "SELECT trial_identifier FROM trials WHERE trial_id = ?", (trial_id,)
        )
        assert cursor.fetchone()[0] == "trial001"

    def test_preserves_autoscaler(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        cursor = db_manager._conn.execute(
            "SELECT autoscaler FROM trials WHERE trial_id = ?", (trial_id,)
        )
        assert cursor.fetchone()[0] == "karpenter"

    def test_sets_record_created_time(self, db_manager):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        after = datetime.now(timezone.utc).replace(microsecond=0)

        cursor = db_manager._conn.execute(
            "SELECT record_created_time FROM trials WHERE trial_id = ?",
            (trial_id,),
        )
        ts_str = cursor.fetchone()[0]
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        assert before <= ts <= after

    def test_sets_last_update_time(self, db_manager):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        after = datetime.now(timezone.utc).replace(microsecond=0)

        cursor = db_manager._conn.execute(
            "SELECT last_update_time FROM trials WHERE trial_id = ?",
            (trial_id,),
        )
        ts_str = cursor.fetchone()[0]
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        assert before <= ts <= after

    def test_nullable_fields_are_null(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        cursor = db_manager._conn.execute(
            """SELECT benchmark_runner_public_ip, ssh_key_pair_name,
                      infra_start_time, infra_end_time, cleanup_start_time,
                      cleanup_end_time, benchmark_start_time, benchmark_end_time
               FROM trials WHERE trial_id = ?""",
            (trial_id,),
        )
        row = cursor.fetchone()
        assert all(field is None for field in row)

    def test_sequential_ids_for_multiple_inserts(self, db_manager):
        id1 = db_manager.insert_trial("run001", "trial001", "karpenter")
        id2 = db_manager.insert_trial("run001", "trial002", "karpenter")
        assert id2 == id1 + 1

    def test_raises_duplicate_trial_error(self, db_manager):
        db_manager.insert_trial("run001", "trial001", "karpenter")
        with pytest.raises(DuplicateTrialError):
            db_manager.insert_trial("run001", "trial001", "cluster-autoscaler")


class TestCheckDuplicateTrial:
    """Tests for check_duplicate_trial."""

    def test_returns_false_when_no_match(self, db_manager):
        assert db_manager.check_duplicate_trial("run001", "trial001") is False

    def test_returns_true_when_match_exists(self, db_manager):
        db_manager.insert_trial("run001", "trial001", "karpenter")
        assert db_manager.check_duplicate_trial("run001", "trial001") is True

    def test_returns_false_for_different_run_identifier(self, db_manager):
        db_manager.insert_trial("run001", "trial001", "karpenter")
        assert db_manager.check_duplicate_trial("run002", "trial001") is False

    def test_returns_false_for_different_trial_identifier(self, db_manager):
        db_manager.insert_trial("run001", "trial001", "karpenter")
        assert db_manager.check_duplicate_trial("run001", "trial002") is False


class TestUpdateTrialAfterApply:
    """Tests for update_trial_after_apply."""

    def test_sets_status_to_init(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        db_manager.update_trial_after_apply(trial_id, "1.2.3.4", "kasbench-key")

        cursor = db_manager._conn.execute(
            "SELECT status FROM trials WHERE trial_id = ?", (trial_id,)
        )
        assert cursor.fetchone()[0] == "INIT"

    def test_sets_benchmark_runner_public_ip(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        db_manager.update_trial_after_apply(trial_id, "1.2.3.4", "kasbench-key")

        cursor = db_manager._conn.execute(
            "SELECT benchmark_runner_public_ip FROM trials WHERE trial_id = ?",
            (trial_id,),
        )
        assert cursor.fetchone()[0] == "1.2.3.4"

    def test_sets_ssh_key_pair_name(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        db_manager.update_trial_after_apply(trial_id, "1.2.3.4", "kasbench-key")

        cursor = db_manager._conn.execute(
            "SELECT ssh_key_pair_name FROM trials WHERE trial_id = ?",
            (trial_id,),
        )
        assert cursor.fetchone()[0] == "kasbench-key"

    def test_updates_last_update_time(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        before = datetime.now(timezone.utc).replace(microsecond=0)
        db_manager.update_trial_after_apply(trial_id, "1.2.3.4", "kasbench-key")
        after = datetime.now(timezone.utc).replace(microsecond=0)

        cursor = db_manager._conn.execute(
            "SELECT last_update_time FROM trials WHERE trial_id = ?",
            (trial_id,),
        )
        ts_str = cursor.fetchone()[0]
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        assert before <= ts <= after

    def test_infra_end_time_remains_null(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        db_manager.update_trial_after_apply(trial_id, "1.2.3.4", "kasbench-key")

        cursor = db_manager._conn.execute(
            "SELECT infra_end_time FROM trials WHERE trial_id = ?", (trial_id,)
        )
        assert cursor.fetchone()[0] is None

    def test_infra_end_time_reset_to_null_if_previously_set(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        # Manually set infra_end_time to simulate a previous value
        db_manager._conn.execute(
            "UPDATE trials SET infra_end_time = '2025-01-01T00:00:00Z' WHERE trial_id = ?",
            (trial_id,),
        )
        db_manager._conn.commit()

        db_manager.update_trial_after_apply(trial_id, "1.2.3.4", "kasbench-key")

        cursor = db_manager._conn.execute(
            "SELECT infra_end_time FROM trials WHERE trial_id = ?", (trial_id,)
        )
        assert cursor.fetchone()[0] is None


class TestRecordInfraStartTime:
    """Tests for record_infra_start_time."""

    def test_sets_infra_start_time(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        before = datetime.now(timezone.utc).replace(microsecond=0)
        db_manager.record_infra_start_time(trial_id)
        after = datetime.now(timezone.utc).replace(microsecond=0)

        cursor = db_manager._conn.execute(
            "SELECT infra_start_time FROM trials WHERE trial_id = ?",
            (trial_id,),
        )
        ts_str = cursor.fetchone()[0]
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        assert before <= ts <= after

    def test_infra_start_time_initially_null(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        cursor = db_manager._conn.execute(
            "SELECT infra_start_time FROM trials WHERE trial_id = ?",
            (trial_id,),
        )
        assert cursor.fetchone()[0] is None

    def test_does_not_modify_other_fields(self, db_manager):
        trial_id = db_manager.insert_trial("run001", "trial001", "karpenter")
        db_manager.record_infra_start_time(trial_id)

        cursor = db_manager._conn.execute(
            "SELECT status, run_identifier, trial_identifier, autoscaler FROM trials WHERE trial_id = ?",
            (trial_id,),
        )
        row = cursor.fetchone()
        assert row == ("PENDING", "run001", "trial001", "karpenter")
