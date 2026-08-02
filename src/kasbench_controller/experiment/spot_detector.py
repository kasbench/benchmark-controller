"""Spot interruption detector - polls EC2 metadata on cluster nodes."""

from __future__ import annotations

import threading
import time
from typing import Callable

import paramiko
import structlog


class SpotInterruptionDetector:
    """Background poller that detects EC2 spot instance termination notices.

    Polls the instance metadata endpoint on cluster nodes via SSH at a
    configurable interval. When a termination notice is detected, sets
    a threading.Event to signal the pipeline.

    The detector runs as a daemon thread and is started/stopped by the
    pipeline around the infrastructure-active steps.
    """

    METADATA_URL = "http://169.254.169.254/latest/meta-data/spot/instance-action"

    def __init__(
        self,
        node_ips: list[str],
        ssh_key_path: str,
        ssh_user: str,
        poll_interval_seconds: int,
        logger: structlog.BoundLogger,
    ) -> None:
        self._node_ips = node_ips
        self._ssh_key_path = ssh_key_path
        self._ssh_user = ssh_user
        self._poll_interval = poll_interval_seconds
        self._logger = logger
        self._interrupt_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._interrupted_node: str | None = None

    @property
    def interrupt_event(self) -> threading.Event:
        """Event that is set when a spot interruption is detected."""
        return self._interrupt_event

    @property
    def interrupted_node(self) -> str | None:
        """The IP of the node that received the termination notice."""
        return self._interrupted_node

    def start(self) -> None:
        """Start the background polling thread."""
        self._stop_event.clear()
        self._interrupt_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="spot-interruption-detector",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("spot_detector_started", node_count=len(self._node_ips))

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval + 5)
            self._thread = None
        self._logger.info("spot_detector_stopped")

    def _poll_loop(self) -> None:
        """Main polling loop, runs in background thread."""
        while not self._stop_event.is_set():
            for node_ip in self._node_ips:
                if self._stop_event.is_set():
                    return
                if self._check_node(node_ip):
                    self._interrupted_node = node_ip
                    self._interrupt_event.set()
                    self._logger.warning(
                        "spot_interruption_detected",
                        node_ip=node_ip,
                    )
                    return
            # Sleep in small increments to allow responsive stop
            self._interruptible_sleep(self._poll_interval)

    def _check_node(self, node_ip: str) -> bool:
        """Check a single node for spot interruption notice via SSH + curl."""
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=node_ip,
                username=self._ssh_user,
                key_filename=self._ssh_key_path,
                timeout=10,
            )
            cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 2 {self.METADATA_URL}"
            _, stdout, _ = client.exec_command(cmd, timeout=15)
            status_code = stdout.read().decode().strip()
            client.close()
            # HTTP 200 means a termination notice is present
            return status_code == "200"
        except Exception as exc:
            self._logger.debug(
                "spot_check_failed",
                node_ip=node_ip,
                error=str(exc),
            )
            return False

    def _interruptible_sleep(self, seconds: int) -> None:
        """Sleep in 1-second increments, checking for stop signal."""
        for _ in range(seconds):
            if self._stop_event.is_set() or self._interrupt_event.is_set():
                return
            time.sleep(1)
