"""Spot interruption detector - polls EC2 metadata on cluster nodes."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import paramiko
import structlog

# Suppress noisy paramiko INFO messages (connection/auth banners)
logging.getLogger("paramiko").setLevel(logging.WARNING)

# Number of consecutive SSH failures before treating a node as evicted.
# With a 15-second poll interval, 3 failures = ~45 seconds of unreachability.
_DEFAULT_UNREACHABLE_THRESHOLD = 3


class SpotInterruptionDetector:
    """Background poller that detects EC2 spot instance termination notices.

    Polls the instance metadata endpoint on cluster nodes via SSH at a
    configurable interval. When a termination notice is detected, sets
    a threading.Event to signal the pipeline.

    Detection has two mechanisms:
    1. **Metadata check**: SSH to the node and curl the instance-action endpoint.
       HTTP 200 means a termination notice is present.
    2. **Unreachability check**: If SSH to a node fails for multiple consecutive
       poll cycles (default: 3), the node is considered evicted. This handles the
       case where AWS terminates the instance without the 2-minute warning being
       observable (e.g., the instance is already gone by the time we poll).

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
        unreachable_threshold: int = _DEFAULT_UNREACHABLE_THRESHOLD,
    ) -> None:
        self._node_ips = node_ips
        self._ssh_key_path = ssh_key_path
        self._ssh_user = ssh_user
        self._poll_interval = poll_interval_seconds
        self._logger = logger
        self._unreachable_threshold = unreachable_threshold
        self._interrupt_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._interrupted_node: str | None = None
        # Track consecutive SSH failures per node. A node must first succeed
        # at least once before unreachability detection activates for it.
        self._consecutive_failures: dict[str, int] = {ip: 0 for ip in node_ips}
        self._has_ever_succeeded: dict[str, bool] = {ip: False for ip in node_ips}

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
        self._logger.info(
            "spot_detector_polling",
            node_count=len(self._node_ips),
            poll_interval=self._poll_interval,
            unreachable_threshold=self._unreachable_threshold,
        )
        while not self._stop_event.is_set():
            for node_ip in self._node_ips:
                if self._stop_event.is_set():
                    return
                result = self._check_node(node_ip)
                if result == "interrupted":
                    self._interrupted_node = node_ip
                    self._interrupt_event.set()
                    self._logger.warning(
                        "spot_interruption_detected",
                        node_ip=node_ip,
                        detection_method="metadata",
                    )
                    return
                elif result == "unreachable":
                    self._consecutive_failures[node_ip] += 1
                    failures = self._consecutive_failures[node_ip]
                    has_succeeded = self._has_ever_succeeded[node_ip]

                    if has_succeeded and failures >= self._unreachable_threshold:
                        self._interrupted_node = node_ip
                        self._interrupt_event.set()
                        self._logger.warning(
                            "spot_interruption_detected",
                            node_ip=node_ip,
                            detection_method="unreachable",
                            consecutive_failures=failures,
                        )
                        return
                    else:
                        self._logger.debug(
                            "spot_node_unreachable",
                            node_ip=node_ip,
                            consecutive_failures=failures,
                            threshold=self._unreachable_threshold,
                            has_ever_succeeded=has_succeeded,
                        )
                else:
                    # result == "ok" — node reachable, no termination notice
                    self._consecutive_failures[node_ip] = 0
                    self._has_ever_succeeded[node_ip] = True

            # Sleep in small increments to allow responsive stop
            self._interruptible_sleep(self._poll_interval)

    def _check_node(self, node_ip: str) -> str:
        """Check a single node for spot interruption notice via SSH + curl.

        Returns:
            "interrupted" - termination notice detected (HTTP 200 from metadata)
            "unreachable" - SSH connection failed (node may be evicted)
            "ok" - node reachable, no termination notice
        """
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
            if status_code == "200":
                return "interrupted"
            return "ok"
        except Exception as exc:
            self._logger.debug(
                "spot_check_failed",
                node_ip=node_ip,
                error=str(exc),
            )
            return "unreachable"

    def _interruptible_sleep(self, seconds: int) -> None:
        """Sleep in 1-second increments, checking for stop signal."""
        for _ in range(seconds):
            if self._stop_event.is_set() or self._interrupt_event.is_set():
                return
            time.sleep(1)
