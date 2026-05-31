"""SQLite schema management and operations for the KASBench Controller."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from kasbench_controller.exceptions import DatabaseError, DuplicateTrialError

VALID_STATUSES = (
    "PENDING",
    "INIT",
    "RUNNING",
    "CLEANUP",
    "SUCCESS",
    "FAIL",
    "TERMINATED",
    "UNKNOWN",
)

_TRIALS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS trials (
    trial_id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'INIT', 'RUNNING', 'CLEANUP', 'SUCCESS', 'FAIL', 'TERMINATED', 'UNKNOWN')),
    run_identifier TEXT,
    trial_identifier TEXT,
    autoscaler TEXT NOT NULL,
    record_created_time DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    benchmark_runner_public_ip TEXT,
    ssh_key_pair_name TEXT,
    last_update_time DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    infra_start_time DATETIME,
    infra_end_time DATETIME,
    cleanup_start_time DATETIME,
    cleanup_end_time DATETIME,
    benchmark_start_time DATETIME,
    benchmark_end_time DATETIME,
    unresponsive_checks INTEGER NOT NULL DEFAULT 0
);
"""

_EVENTS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id INTEGER NOT NULL,
    event_time DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    event_type TEXT NOT NULL,
    event_request TEXT,
    event_message TEXT,
    FOREIGN KEY (trial_id) REFERENCES trials(trial_id)
);
"""


class DatabaseManager:
    """Manages the SQLite benchmark database schema and operations."""

    def __init__(self, db_path: Path) -> None:
        """Open a connection to the database with foreign keys enabled.

        Args:
            db_path: Path to the SQLite database file.

        Raises:
            DatabaseError: If the connection cannot be established.
        """
        self._db_path = db_path
        try:
            self._conn = sqlite3.connect(str(db_path))
            self._conn.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to open database at {db_path}: {e}"
            ) from e

    def create_schema(self) -> None:
        """Create the trials and events tables if they do not exist.

        Raises:
            DatabaseError: If schema creation fails.
        """
        try:
            with self._conn:
                self._conn.execute(_TRIALS_SCHEMA)
                self._conn.execute(_EVENTS_SCHEMA)
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to create schema in {self._db_path}: {e}"
            ) from e

    def verify_schema(self) -> bool:
        """Confirm that both the trials and events tables exist.

        Returns:
            True if both tables exist, False otherwise.

        Raises:
            DatabaseError: If the verification query fails.
        """
        try:
            cursor = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('trials', 'events')"
            )
            tables = {row[0] for row in cursor.fetchall()}
            return "trials" in tables and "events" in tables
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to verify schema in {self._db_path}: {e}"
            ) from e

    def insert_trial(
        self, run_identifier: str, trial_identifier: str, autoscaler: str
    ) -> int:
        """Insert a new trial record with status PENDING.

        Args:
            run_identifier: The run this trial belongs to.
            trial_identifier: Unique identifier for the trial within the run.
            autoscaler: Name of the autoscaler being tested.

        Returns:
            The generated trial_id for the new record.

        Raises:
            DuplicateTrialError: If a trial with the same run_identifier and
                trial_identifier already exists.
            DatabaseError: If the insert operation fails.
        """
        if self.check_duplicate_trial(run_identifier, trial_identifier):
            raise DuplicateTrialError(
                f"Trial with run_identifier='{run_identifier}' and "
                f"trial_identifier='{trial_identifier}' already exists"
            )

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            with self._conn:
                cursor = self._conn.execute(
                    """INSERT INTO trials
                       (status, run_identifier, trial_identifier, autoscaler,
                        record_created_time, last_update_time)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("PENDING", run_identifier, trial_identifier, autoscaler, now, now),
                )
                return cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to insert trial in {self._db_path}: {e}"
            ) from e

    def check_duplicate_trial(
        self, run_identifier: str, trial_identifier: str
    ) -> bool:
        """Check if a trial with the given identifiers already exists.

        Args:
            run_identifier: The run identifier to check.
            trial_identifier: The trial identifier to check.

        Returns:
            True if a matching record exists, False otherwise.

        Raises:
            DatabaseError: If the query fails.
        """
        try:
            cursor = self._conn.execute(
                """SELECT 1 FROM trials
                   WHERE run_identifier = ? AND trial_identifier = ?
                   LIMIT 1""",
                (run_identifier, trial_identifier),
            )
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to check duplicate trial in {self._db_path}: {e}"
            ) from e

    def update_trial_after_apply(
        self, trial_id: int, public_ip: str, key_pair_name: str
    ) -> None:
        """Update a trial record after successful tofu apply.

        Sets status to INIT, records the benchmark runner public IP and SSH
        key pair name, and updates last_update_time. infra_end_time remains NULL.

        Args:
            trial_id: The trial to update.
            public_ip: The benchmark runner's public IP address.
            key_pair_name: The SSH key pair name used for the trial.

        Raises:
            DatabaseError: If the update operation fails.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            with self._conn:
                self._conn.execute(
                    """UPDATE trials
                       SET status = 'INIT',
                           benchmark_runner_public_ip = ?,
                           ssh_key_pair_name = ?,
                           last_update_time = ?,
                           infra_end_time = NULL
                       WHERE trial_id = ?""",
                    (public_ip, key_pair_name, now, trial_id),
                )
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to update trial {trial_id} in {self._db_path}: {e}"
            ) from e

    def record_infra_start_time(self, trial_id: int) -> None:
        """Record the infrastructure start time for a trial.

        Sets infra_start_time to the current UTC timestamp.

        Args:
            trial_id: The trial to update.

        Raises:
            DatabaseError: If the update operation fails.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            with self._conn:
                self._conn.execute(
                    """UPDATE trials
                       SET infra_start_time = ?
                       WHERE trial_id = ?""",
                    (now, trial_id),
                )
        except sqlite3.Error as e:
            raise DatabaseError(
                f"Failed to record infra start time for trial {trial_id} "
                f"in {self._db_path}: {e}"
            ) from e
