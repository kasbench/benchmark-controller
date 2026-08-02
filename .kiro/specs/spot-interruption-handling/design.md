# Design Document: Spot Interruption Handling

## Architecture Overview

This feature adds a cooperative spot instance interruption detection and handling layer to the existing experiment orchestrator. The architecture follows a **producer-consumer pattern**: a background polling thread (the detector) produces interruption signals, and the pipeline/orchestrator consume those signals to trigger abort, cooldown, and schedule replacement logic.

Key architectural decisions:

1. **Controller-side polling via SSH**: Since the controller runs on a separate machine from the cluster nodes, the detector uses SSH (paramiko, already a project dependency) to query the EC2 instance metadata endpoint on cluster nodes. This avoids requiring any agent installation on cluster nodes.

2. **Threading.Event for signaling**: A `threading.Event` provides the cross-thread communication between the polling thread and the pipeline execution thread. This is lightweight, thread-safe, and allows the pipeline to check for interruption between step boundaries and during long-running steps.

3. **Orchestrator-level schedule mutation**: The schedule is extended dynamically by the orchestrator (not the scheduler), keeping the scheduler as a pure schedule generator and allowing the orchestrator to manage trial numbering consistently.

4. **Existing AbortSequence reuse**: The spot interruption abort path reuses the existing two-tier AbortSequence, inserting itself naturally into the existing error-handling flow.

## Component Design

### 1. SpotInterruptionDetector

A new class that polls EC2 instance metadata on cluster nodes via SSH to detect spot termination notices.

```python
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
```

### 2. Pipeline Modifications (TrialPipeline)

The pipeline gains awareness of the interrupt signal. It checks the event between steps and during long-running steps.

```python
# New constants added to TrialPipeline
INFRASTRUCTURE_ACTIVE_STEPS = [
    "build-infrastructure",
    "wait",
    "initialize-runner",
    "benchmark-start",
    "benchmark-monitor",
    "benchmark-postprocessing",
    "shutdown",
    "destroy-infrastructure",
]

# New parameter in __init__
def __init__(
    self,
    ...,
    interrupt_event: threading.Event | None = None,
) -> None:
    ...
    self._interrupt_event = interrupt_event

# Modified execute() loop — check interrupt between steps
def execute(self, start_from_step: str | None = None) -> TrialResult:
    steps_to_execute = self._resolve_steps(start_from_step)

    for step in steps_to_execute:
        # Check for spot interruption before starting step
        if self._interrupt_event and self._interrupt_event.is_set():
            return TrialResult(
                trial_identifier=self._assignment.trial_identifier,
                autoscaler=self._assignment.autoscaler,
                success=False,
                failed_step=step,
                error_message="spot-interruption",
            )

        # ... execute step as before ...

        # Check for spot interruption after step completes
        if self._interrupt_event and self._interrupt_event.is_set():
            return TrialResult(
                trial_identifier=self._assignment.trial_identifier,
                autoscaler=self._assignment.autoscaler,
                success=False,
                failed_step=step,
                error_message="spot-interruption",
            )

    return TrialResult(...)
```

### 3. Orchestrator Modifications (ExperimentOrchestrator)

The orchestrator adds:
- SpotInterruptionDetector lifecycle management
- Consecutive interruption counter
- Cooldown enforcement
- Dynamic schedule extension

```python
class ExperimentOrchestrator:
    def run(self) -> int:
        ...
        consecutive_interruptions = 0

        for i in range(trial_index, len(schedule)):
            assignment = schedule[i]

            # Start spot detector for this trial
            detector = self._create_detector(assignment)
            if detector:
                detector.start()

            # Create pipeline with interrupt event
            pipeline = TrialPipeline(
                ...,
                interrupt_event=detector.interrupt_event if detector else None,
            )

            result = pipeline.execute(start_from_step=current_step)

            # Stop detector after trial
            if detector:
                detector.stop()

            if not result.success and result.error_message == "spot-interruption":
                # Handle spot interruption
                consecutive_interruptions += 1

                # Record as aborted
                progress_manager.record_trial_aborted(
                    trial_id=result.trial_identifier,
                    reason="spot-interruption",
                    failed_step=result.failed_step,
                )

                # Run abort sequence
                abort_result = abort_sequence.execute(
                    trial_identifier=result.trial_identifier,
                    autoscaler=result.autoscaler,
                )
                if abort_result.must_halt:
                    return 1

                # Check retry cap
                max_consecutive = self._config.spot_max_consecutive_interruptions
                if max_consecutive > 0 and consecutive_interruptions >= max_consecutive:
                    self._logger.error("spot_retry_cap_reached", ...)
                    progress_manager.persist()
                    return 1

                # Append replacement trial
                new_trial = self._append_replacement(
                    schedule, assignment.autoscaler, progress_manager
                )

                # Cooldown
                self._cooldown(self._config.spot_cooldown_seconds)

            elif result.success:
                consecutive_interruptions = 0
            else:
                # Normal failure handling (existing logic)
                ...
```

### 4. ExperimentConfig Extensions

```python
@dataclass
class ExperimentConfig:
    ...
    # Spot interruption handling
    spot_cooldown_seconds: int = 600
    spot_max_consecutive_interruptions: int = 3
    spot_poll_interval_seconds: int = 15
```

### 5. Model Extensions

```python
@dataclass
class TrialResult:
    trial_identifier: str
    autoscaler: str
    success: bool
    failed_step: str | None = None
    error_message: str | None = None
    status: str = "completed"  # "completed", "failed", "aborted"
```

### 6. ProgressManager Extensions

```python
class ProgressManager:
    def record_trial_aborted(
        self, trial_id: str, reason: str, failed_step: str | None = None
    ) -> None:
        """Record a trial as aborted with the given reason.

        Preserves any step history already recorded for this trial.
        Adds an 'aborted' marker with reason and timestamp.
        """
        ...

    def is_complete(self) -> bool:
        """Check if all non-aborted trials have completed successfully.

        Trials with status 'aborted' are excluded from completion checks.
        Only trials in the schedule that are not marked aborted must have
        all steps completed as 'success'.
        """
        ...

    def find_resume_point(self, rerun_from_failed: bool) -> tuple[int, str | None]:
        """Find resume point, skipping aborted trials."""
        ...

    def append_to_schedule(self, trial_identifier: str, autoscaler: str) -> None:
        """Append a replacement trial to the schedule and persist."""
        ...
```

### 7. Scheduler — No Changes Required

The `TrialScheduler` remains a pure schedule generator. Schedule extension is handled by the orchestrator calling `ProgressManager.append_to_schedule()` directly, which maintains consistency with the persisted progress state.

## Data Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                     ExperimentOrchestrator                         │
│                                                                    │
│  for each trial:                                                   │
│    1. Create SpotInterruptionDetector (SSH to cluster nodes)       │
│    2. Start detector thread                                        │
│    3. Create TrialPipeline (passing interrupt_event)               │
│    4. pipeline.execute()                                           │
│    5. Stop detector thread                                         │
│    6. If spot-interruption:                                        │
│       a. Record aborted status                                     │
│       b. Run AbortSequence                                         │
│       c. Check retry cap                                           │
│       d. Append replacement trial                                  │
│       e. Wait cooldown                                             │
│    7. If success: reset consecutive counter                        │
└──────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│         SpotInterruptionDetector             │
│                                              │
│  Background thread:                          │
│    while not stopped:                        │
│      for node_ip in cluster_nodes:           │
│        SSH → curl instance-action endpoint   │
│        if HTTP 200 → set interrupt_event     │
│      sleep(poll_interval)                    │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│            TrialPipeline                     │
│                                              │
│  for each step:                              │
│    check interrupt_event (early exit)        │
│    execute step                              │
│    check interrupt_event (early exit)        │
│    record step result                        │
└─────────────────────────────────────────────┘
```

## Error Handling

| Scenario | Behavior |
|----------|----------|
| SSH connection to node fails during polling | Log debug, skip node, continue polling other nodes |
| Spot interruption during build-infrastructure | Pipeline returns aborted, AbortSequence cleans up |
| Spot interruption during benchmark-monitor | Pipeline returns aborted, AbortSequence cleans up |
| AbortSequence fails (must_halt) after spot abort | Orchestrator halts immediately, persists state |
| Consecutive interruption cap reached | Orchestrator halts, persists state, logs retry cap error |
| Detector thread crashes | Pipeline continues without detection; normal error handling applies if infra vanishes |
| Cooldown interrupted by SIGINT | Standard signal handling applies; experiment can be resumed |

## Configuration Defaults

| Parameter | Default | Description |
|-----------|---------|-------------|
| `spot_cooldown_seconds` | 600 | Wait time after spot abort before next trial |
| `spot_max_consecutive_interruptions` | 3 | Halt after N consecutive interruptions (0 = disable) |
| `spot_poll_interval_seconds` | 15 | How often to check EC2 metadata on each node |

## Interface Contracts

### SpotInterruptionDetector

- **Input**: List of node IPs, SSH key path, SSH user, poll interval
- **Output**: `interrupt_event` (threading.Event), `interrupted_node` (str | None)
- **Lifecycle**: `start()` → runs until `stop()` or interruption detected

### Orchestrator → ProgressManager

- `record_trial_aborted(trial_id, reason, failed_step)` — records aborted trial
- `append_to_schedule(trial_identifier, autoscaler)` — extends schedule, persists
- `is_complete()` — excludes aborted trials from completion check
- `find_resume_point()` — skips aborted trials

### Orchestrator → Pipeline

- Pipeline constructor accepts optional `interrupt_event: threading.Event`
- Pipeline returns `TrialResult` with `error_message="spot-interruption"` for spot aborts

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Detection active only during infrastructure steps

*For any* pipeline step, the SpotInterruptionDetector is running (polling) if and only if the step is in the infrastructure-active range (build-infrastructure through destroy-infrastructure).

**Validates: Requirements 1.1**

### Property 2: Detection latency bounded by poll interval

*For any* poll interval P and any moment when a spot termination notice becomes available, the interrupt_event is set within at most P seconds.

**Validates: Requirements 1.2**

### Property 3: Pipeline terminates on interrupt signal

*For any* pipeline execution where the interrupt_event is set, the pipeline stops executing steps and returns a TrialResult with error_message="spot-interruption" without proceeding to subsequent steps.

**Validates: Requirements 2.1**

### Property 4: Abort sequence invoked on spot interruption

*For any* trial terminated due to spot interruption, the AbortSequence.execute() is called with the trial's identifier and autoscaler before cooldown begins.

**Validates: Requirements 2.3**

### Property 5: must_halt propagation

*For any* spot-interruption abort where the AbortSequence returns must_halt=True, the orchestrator halts the experiment and returns exit code 1.

**Validates: Requirements 2.4**

### Property 6: Schedule replacement preserves autoscaler and extends correctly

*For any* trial aborted due to spot interruption with autoscaler A in a schedule of current length N, the orchestrator appends exactly one new trial at the end of the schedule with the same autoscaler A and trial identifier equal to trial_prefix + zero-padded (N+1).

**Validates: Requirements 3.1, 3.2**

### Property 7: Aborted trial correctly recorded

*For any* trial aborted due to spot interruption, the progress state contains an entry for that trial with status "aborted", reason "spot-interruption", and all step records up to the point of interruption preserved.

**Validates: Requirements 2.2, 6.1, 6.4**

### Property 8: Aborted trials excluded from completion evaluation

*For any* progress state containing trials with status "aborted", those trials are not counted as successful completions. The is_complete function returns True only when all non-aborted trials in the schedule have all steps recorded as "success".

**Validates: Requirements 3.4, 6.2**

### Property 9: Consecutive interruption counter invariant

*For any* sequence of trial outcomes where each outcome is either "success" or "spot-abort", the consecutive interruption counter at each point equals the length of the current run of consecutive spot-aborts. It resets to 0 after any success, and when it reaches spot_max_consecutive_interruptions (if > 0), the experiment halts.

**Validates: Requirements 5.1, 5.2, 5.3**

### Property 10: Cooldown enforced after spot abort

*For any* spot-interrupted trial where the AbortSequence completes successfully, the orchestrator waits at least spot_cooldown_seconds before starting the next trial.

**Validates: Requirements 4.1**

### Property 11: Resume skips aborted trials

*For any* experiment progress state being resumed, find_resume_point returns the index of the first trial that is neither completed nor aborted, including any appended replacement trials.

**Validates: Requirements 6.3**

### Property 12: Progress persisted after schedule modification

*For any* schedule modification (appending a replacement trial) or experiment halt (due to retry cap), the progress state is persisted to S3 before the orchestrator continues or exits.

**Validates: Requirements 3.3, 5.4**

### Property 13: Retry cap disabled when configured to zero

*For any* experiment with spot_max_consecutive_interruptions set to 0, the orchestrator never halts due to consecutive spot interruptions regardless of how many occur in sequence.

**Validates: Requirements 7.4**
