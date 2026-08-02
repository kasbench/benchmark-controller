# Implementation Plan: Spot Interruption Handling

## Overview

Add spot instance interruption detection and handling to the experiment orchestrator. The implementation follows a layered approach: first extending configuration and models, then building the detector, modifying the pipeline to accept interrupt signals, extending the progress manager for aborted trial tracking, and finally wiring everything together in the orchestrator with cooldown, retry cap, and schedule replacement logic.

## Tasks

- [x] 1. Extend configuration and models with spot-related fields
  - [x] 1.1 Add spot configuration parameters to ExperimentConfig
    - Add `spot_cooldown_seconds: int = 600` field
    - Add `spot_max_consecutive_interruptions: int = 3` field
    - Add `spot_poll_interval_seconds: int = 15` field
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 1.2 Extend TrialResult model with status field
    - Add `status: str = "completed"` field to TrialResult dataclass
    - Status values: "completed", "failed", "aborted"
    - _Requirements: 2.2_

- [x] 2. Implement SpotInterruptionDetector
  - [x] 2.1 Create the SpotInterruptionDetector class
    - Create new file `src/kasbench_controller/experiment/spot_detector.py`
    - Implement SSH-based polling via paramiko to query EC2 instance metadata endpoint (`http://169.254.169.254/latest/meta-data/spot/instance-action`)
    - Use `threading.Event` for cross-thread signaling
    - Implement `start()` to launch daemon polling thread
    - Implement `stop()` to signal thread shutdown and join
    - Implement `_poll_loop()` with interruptible sleep
    - Implement `_check_node(node_ip)` using SSH + curl to query metadata
    - HTTP 200 response indicates a termination notice is present
    - Log debug on SSH failures, skip node, continue polling
    - _Requirements: 1.1, 1.2, 1.3_

  - [x]* 2.2 Write unit tests for SpotInterruptionDetector
    - **Property 1: Detection active only during infrastructure steps**
    - **Property 2: Detection latency bounded by poll interval**
    - Test that `interrupt_event` is set when a node returns HTTP 200
    - Test that SSH connection failures are handled gracefully (skip node, continue)
    - Test that `stop()` terminates the polling thread
    - Test `_interruptible_sleep` responds to stop signal
    - Mock paramiko SSH connections
    - _Requirements: 1.1, 1.2, 1.3_

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Modify TrialPipeline to accept and check interrupt_event
  - [x] 4.1 Add interrupt_event parameter and inter-step checking to TrialPipeline
    - Add optional `interrupt_event: threading.Event | None = None` parameter to `__init__`
    - In `execute()`, check `interrupt_event.is_set()` before each step
    - In `execute()`, check `interrupt_event.is_set()` after each step completes
    - When interrupt detected, return `TrialResult` with `success=False`, `error_message="spot-interruption"`, `status="aborted"`
    - Record successful steps in progress before checking interrupt (preserve step history)
    - _Requirements: 2.1, 2.2, 6.4_

  - [x]* 4.2 Write unit tests for pipeline interrupt handling
    - **Property 3: Pipeline terminates on interrupt signal**
    - Test that pipeline exits early when interrupt_event is already set before execution
    - Test that pipeline exits early when interrupt_event is set between steps
    - Test that TrialResult has correct error_message and status values on interrupt
    - Test that step history is preserved for completed steps before interruption
    - Test that pipeline works normally when interrupt_event is None (backward compatible)
    - _Requirements: 2.1, 2.2, 6.4_

- [x] 5. Extend ProgressManager with aborted trial recording and schedule extension
  - [x] 5.1 Add record_trial_aborted method to ProgressManager
    - Implement `record_trial_aborted(trial_id, reason, failed_step)` method
    - Record trial entry with status "aborted", reason field, and timestamp
    - Preserve any existing step history for the trial
    - Persist updated state to S3
    - _Requirements: 6.1, 6.4_

  - [x] 5.2 Add append_to_schedule method to ProgressManager
    - Implement `append_to_schedule(trial_identifier, autoscaler)` method
    - Append new trial assignment to the schedule list in progress state
    - Persist updated state to S3 immediately
    - _Requirements: 3.3_

  - [x] 5.3 Modify is_complete to exclude aborted trials
    - Update `is_complete()` to skip trials with status "aborted" in their trial_results
    - Only require successful completion for non-aborted trials in the schedule
    - _Requirements: 3.4, 6.2_

  - [x] 5.4 Modify find_resume_point to skip aborted trials
    - Update `find_resume_point()` to skip trials marked as "aborted"
    - Continue to the next pending trial in the schedule (including appended replacements)
    - _Requirements: 6.3_

  - [x]* 5.5 Write unit tests for ProgressManager extensions
    - **Property 7: Aborted trial correctly recorded**
    - **Property 8: Aborted trials excluded from completion evaluation**
    - **Property 11: Resume skips aborted trials**
    - **Property 12: Progress persisted after schedule modification**
    - Test `record_trial_aborted` creates correct entry with reason and preserved step history
    - Test `append_to_schedule` extends schedule and triggers persist
    - Test `is_complete` returns False when non-aborted trials are incomplete
    - Test `is_complete` returns True when only aborted trials remain incomplete
    - Test `find_resume_point` skips aborted trials and returns next pending
    - _Requirements: 3.3, 3.4, 6.1, 6.2, 6.3, 6.4_

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Modify ExperimentOrchestrator for spot interruption lifecycle management
  - [x] 7.1 Add detector lifecycle and interrupt detection to orchestrator trial loop
    - Import SpotInterruptionDetector
    - Before each trial, resolve cluster node IPs (from Terraform outputs)
    - Create and start SpotInterruptionDetector with node IPs, SSH key, user, poll interval from config
    - Pass `detector.interrupt_event` to TrialPipeline constructor
    - Stop detector after trial completes (success or failure)
    - _Requirements: 1.1_

  - [x] 7.2 Add spot-interruption-specific handling branch to orchestrator
    - Detect spot interruptions via `result.error_message == "spot-interruption"`
    - Increment consecutive_interruptions counter
    - Call `progress_manager.record_trial_aborted(trial_id, "spot-interruption", failed_step)`
    - Invoke AbortSequence; if `must_halt`, return 1
    - _Requirements: 2.3, 2.4, 5.1, 6.1_

  - [x] 7.3 Implement retry cap check in orchestrator
    - After incrementing consecutive_interruptions, check against `config.spot_max_consecutive_interruptions`
    - If cap > 0 and counter >= cap, log error, persist progress, return 1
    - If cap == 0, skip the check (unlimited retries)
    - _Requirements: 5.3, 5.4, 7.4_

  - [x] 7.4 Implement schedule replacement for aborted trials
    - Compute new trial identifier: `trial_prefix + zero-padded(len(schedule) + 1)`
    - Call `progress_manager.append_to_schedule(new_trial_id, assignment.autoscaler)`
    - Append new `TrialAssignment` to the in-memory schedule list so the loop iterates over it
    - _Requirements: 3.1, 3.2, 3.3_

  - [x] 7.5 Implement cooldown wait after spot interruption
    - After abort completes and replacement is appended, sleep for `config.spot_cooldown_seconds`
    - Log remaining cooldown time at periodic intervals (every 60 seconds)
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 7.6 Reset consecutive interruption counter on successful trial
    - After a trial completes successfully, set `consecutive_interruptions = 0`
    - _Requirements: 5.2_

  - [x]* 7.7 Write unit tests for orchestrator spot-interruption logic
    - **Property 4: Abort sequence invoked on spot interruption**
    - **Property 5: must_halt propagation**
    - **Property 6: Schedule replacement preserves autoscaler and extends correctly**
    - **Property 9: Consecutive interruption counter invariant**
    - **Property 10: Cooldown enforced after spot abort**
    - **Property 13: Retry cap disabled when configured to zero**
    - Test that detector is started before trial and stopped after
    - Test that spot interruption triggers record_trial_aborted
    - Test that AbortSequence is called on spot interruption
    - Test that must_halt from AbortSequence causes exit code 1
    - Test that retry cap halts experiment after N consecutive interruptions
    - Test that retry cap is disabled when set to 0
    - Test that replacement trial is appended with correct autoscaler and identifier
    - Test that cooldown sleep is called with configured duration
    - Test that consecutive counter resets on success
    - _Requirements: 2.3, 2.4, 3.1, 3.2, 4.1, 5.1, 5.2, 5.3, 5.4, 7.4_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The SpotInterruptionDetector uses paramiko (already a project dependency) for SSH connectivity
- The orchestrator resolves node IPs from Terraform outputs in the trial's working directory
- All modifications are backward-compatible: when no interrupt_event is provided, existing behavior is preserved

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1", "5.1", "5.2", "5.3", "5.4"] },
    { "id": 2, "tasks": ["2.2", "4.1", "5.5"] },
    { "id": 3, "tasks": ["4.2", "7.1"] },
    { "id": 4, "tasks": ["7.2", "7.3", "7.4", "7.5", "7.6"] },
    { "id": 5, "tasks": ["7.7"] }
  ]
}
```
