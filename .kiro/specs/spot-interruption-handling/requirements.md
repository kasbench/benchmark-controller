# Requirements Document

## Introduction

This feature adds spot instance interruption handling to the kasbench-controller experiment orchestrator. When AWS reclaims a spot instance during a trial, the system detects the 2-minute termination warning via EC2 instance metadata polling, aborts the affected trial, performs infrastructure cleanup, waits a configurable cooldown period, and appends a replacement trial to the schedule. A retry cap prevents infinite loops when spot capacity is persistently unavailable.

## Glossary

- **Spot_Interruption_Detector**: The component that polls EC2 instance metadata on cluster nodes to detect spot instance termination notices.
- **Orchestrator**: The ExperimentOrchestrator that coordinates multi-trial experiment execution, schedule management, and error handling.
- **Pipeline**: The TrialPipeline that executes a single trial through the 9-step benchmark lifecycle.
- **AbortSequence**: The existing two-tier infrastructure cleanup mechanism invoked when a trial fails.
- **ProgressManager**: The component that persists experiment state to S3 for resumption.
- **Scheduler**: The TrialScheduler that generates and manages the trial-to-autoscaler schedule.
- **Spot_Interruption_Notice**: The 2-minute advance warning provided by EC2 instance metadata when a spot instance is scheduled for reclamation.
- **Cooldown_Period**: A configurable wait duration after a spot reclamation event before the next trial begins.
- **Consecutive_Interruption_Counter**: A counter tracking the number of consecutive spot interruptions without a successful trial completion.

## Requirements

### Requirement 1: Spot Interruption Detection

**User Story:** As a researcher, I want the system to detect spot instance termination warnings on cluster nodes, so that interrupted trials are identified immediately rather than failing with ambiguous errors.

#### Acceptance Criteria

1. WHILE a trial is executing pipeline steps that use cluster infrastructure (build-infrastructure through destroy-infrastructure), THE Spot_Interruption_Detector SHALL poll EC2 instance metadata on cluster nodes at a regular interval to check for spot interruption notices.
2. WHEN the Spot_Interruption_Detector receives a spot termination notice from EC2 instance metadata, THE Spot_Interruption_Detector SHALL signal the Pipeline within 30 seconds of the notice becoming available.
3. THE Spot_Interruption_Detector SHALL use the EC2 instance metadata endpoint to retrieve the instance-action scheduled events for cluster nodes.

### Requirement 2: Trial Abort on Spot Interruption

**User Story:** As a researcher, I want interrupted trials to be immediately terminated and marked as aborted, so that partial or corrupted benchmark results are never counted as valid data.

#### Acceptance Criteria

1. WHEN the Spot_Interruption_Detector signals a spot interruption during a trial, THE Pipeline SHALL immediately terminate the current step execution for that trial.
2. WHEN a trial is terminated due to a spot interruption, THE Pipeline SHALL set the trial status to "aborted".
3. WHEN a trial is terminated due to a spot interruption, THE Pipeline SHALL invoke the existing AbortSequence to perform infrastructure cleanup before the cooldown period begins.
4. IF the AbortSequence returns must_halt after a spot-interruption-triggered abort, THEN THE Orchestrator SHALL halt the experiment with an appropriate error message.

### Requirement 3: Schedule Replacement for Aborted Trials

**User Story:** As a researcher, I want aborted trials to be replaced with new trials for the same autoscaler, so that each autoscaler achieves the required number of successful repetitions.

#### Acceptance Criteria

1. WHEN a trial is aborted due to a spot interruption, THE Orchestrator SHALL append a replacement trial assignment for the same autoscaler to the end of the schedule.
2. THE Orchestrator SHALL assign the replacement trial a new sequential trial identifier following the existing trial_prefix and numbering convention.
3. WHEN a replacement trial is appended, THE Orchestrator SHALL persist the updated schedule to the progress state via the ProgressManager.
4. THE Orchestrator SHALL NOT count aborted trials toward the trials_per_autoscaler completion count.

### Requirement 4: Cooldown Period After Spot Reclamation

**User Story:** As a researcher, I want a configurable wait period after spot interruptions, so that the system does not immediately re-provision into the same capacity shortage.

#### Acceptance Criteria

1. WHEN a trial is aborted due to a spot interruption and the AbortSequence completes, THE Orchestrator SHALL wait for the configured Cooldown_Period before starting the next trial.
2. THE Orchestrator SHALL support a configurable cooldown duration with a default value of 600 seconds (10 minutes).
3. WHILE the Orchestrator is in the cooldown period, THE Orchestrator SHALL log the remaining cooldown time at periodic intervals.

### Requirement 5: Consecutive Interruption Retry Cap

**User Story:** As a researcher, I want the experiment to halt after repeated consecutive spot interruptions, so that the system does not loop indefinitely when spot capacity is unavailable.

#### Acceptance Criteria

1. THE Orchestrator SHALL maintain a Consecutive_Interruption_Counter that tracks the number of consecutive spot interruptions without a successful trial completion.
2. WHEN a trial completes successfully, THE Orchestrator SHALL reset the Consecutive_Interruption_Counter to zero.
3. WHEN the Consecutive_Interruption_Counter reaches 3, THE Orchestrator SHALL halt the experiment and log an error message indicating that the retry cap has been reached.
4. WHEN the experiment halts due to the retry cap, THE Orchestrator SHALL persist the current progress state so the experiment can be resumed later.

### Requirement 6: Progress Tracking for Aborted Trials

**User Story:** As a researcher, I want aborted trials to be recorded in the progress state, so that I have a complete audit trail of all experiment events including interruptions.

#### Acceptance Criteria

1. WHEN a trial is aborted due to a spot interruption, THE ProgressManager SHALL record the trial in the progress state with status "aborted" and the reason "spot-interruption".
2. THE ProgressManager SHALL NOT count trials with status "aborted" as successful completions when evaluating experiment progress via is_complete.
3. WHEN the experiment is resumed, THE Orchestrator SHALL skip aborted trials and continue from the next pending trial in the schedule (including any appended replacements).
4. THE ProgressManager SHALL include the aborted trial's step history up to the point of interruption in the persisted progress state.

### Requirement 7: Configuration Parameters

**User Story:** As a researcher, I want spot interruption handling parameters to be configurable, so that I can tune behavior based on my AWS environment and experiment requirements.

#### Acceptance Criteria

1. THE ExperimentConfig SHALL accept a spot_cooldown_seconds parameter with a default value of 600.
2. THE ExperimentConfig SHALL accept a spot_max_consecutive_interruptions parameter with a default value of 3.
3. THE ExperimentConfig SHALL accept a spot_poll_interval_seconds parameter that controls the metadata polling frequency.
4. WHEN spot_max_consecutive_interruptions is set to 0, THE Orchestrator SHALL disable the retry cap and allow unlimited consecutive interruptions.
