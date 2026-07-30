# Implementation Plan: run-experiment

## Overview

Implement the `run-experiment` CLI command that orchestrates multi-trial Kubernetes autoscaler benchmarks. The implementation follows the design's architecture: extract service functions from existing Click commands, build foundational data models and components (ExperimentConfig, TrialScheduler, ProgressManager, AbortSequence, TrialPipeline), compose them in an ExperimentOrchestrator, and wire everything into the CLI.

## Tasks

- [ ] 1. Define ExperimentConfig and data models
  - [ ] 1.1 Create `src/kasbench_controller/experiment/config.py` with ExperimentConfig dataclass
    - Define all fields matching the design (run_identifier, trial_prefix, autoscalers, trials_per_autoscaler, run_duration, working_directory, s3_bucket, aws_region, var_files, variables, auto_approve, runner_version, health_timeout, rollout_timeout, cluster_cidr_range, role_params, random_seed, ebs_wait, rerun_from_failed, halt_on_error)
    - Add `total_trials` property that computes `len(autoscalers) * trials_per_autoscaler`
    - Add validation in `__post_init__` for trial count limit (max 9999)
    - _Requirements: 1.1–1.24, 2.3, 2.4_

  - [ ] 1.2 Create `src/kasbench_controller/experiment/__init__.py` and `src/kasbench_controller/experiment/models.py`
    - Define `TrialAssignment` dataclass (trial_identifier, autoscaler, sequence_number)
    - Define `TrialResult` dataclass (trial_identifier, autoscaler, success, failed_step, error_message)
    - Define `AbortResult` dataclass (success, tier_reached, must_halt, error_message)
    - Define `ExperimentProgress` dataclass (parameters, schedule, trial_results, effective_seed, version)
    - _Requirements: 2.1, 4.14, 5.1, 6.1_

  - [ ]* 1.3 Write property tests for ExperimentConfig validation
    - **Property 1: String parameter validation accepts valid and rejects invalid inputs**
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 1.4 Write property tests for autoscaler and var validation
    - **Property 2: Autoscaler list parsing correctly validates entries**
    - **Property 5: Variable assignment validation**
    - **Validates: Requirements 1.3, 1.4, 1.12**

- [ ] 2. Implement TrialScheduler
  - [ ] 2.1 Create `src/kasbench_controller/experiment/scheduler.py`
    - Implement `TrialScheduler.__init__` accepting ExperimentConfig
    - Implement `generate_schedule()` using Fisher-Yates shuffle on a pool of autoscaler entries
    - Implement `effective_seed` property (use provided seed or generate from non-deterministic source, log it)
    - Generate trial identifiers as `{prefix}{seq:04d}` numbered from 0001
    - Seed random number generator with provided or auto-generated seed
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 2.2 Write property tests for trial schedule generation
    - **Property 6: Trial schedule generation produces correct count and format**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4**

  - [ ]* 2.3 Write property tests for autoscaler assignment balance
    - **Property 7: Autoscaler assignment is balanced**
    - **Validates: Requirements 3.1, 3.4, 3.5, 1.24**

  - [ ]* 2.4 Write property tests for schedule determinism
    - **Property 8: Schedule generation is deterministic given the same seed**
    - **Validates: Requirements 3.2**

- [ ] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Implement ProgressManager
  - [ ] 4.1 Create `src/kasbench_controller/experiment/progress.py`
    - Implement `ProgressManager.__init__` with s3_bucket, aws_region, run_identifier
    - Implement `load_or_create()` that checks S3 for existing progress file or creates new one
    - Implement `record_step_result()` that updates in-memory state and persists to S3
    - Implement `find_resume_point()` that locates first incomplete trial and optionally the failed step
    - Implement `validate_parameters()` that compares stored params vs current (excluding halt_on_error, rerun_from_failed)
    - Implement `is_complete()` check
    - Implement S3 upload with retry (3 attempts, 5s delay)
    - Handle malformed JSON progress file with clear error
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11_

  - [ ]* 4.2 Write property tests for parameter comparison
    - **Property 12: Parameter comparison detects all differences**
    - **Validates: Requirements 6.4, 6.5**

  - [ ]* 4.3 Write property tests for resume point identification
    - **Property 13: Resume point identification**
    - **Validates: Requirements 6.7, 6.8**

- [ ] 5. Extract service functions from existing commands
  - [ ] 5.1 Create `src/kasbench_controller/services/__init__.py` and extract `run_init` service
    - Extract core logic from `commands/init.py` into `services/init_service.py`
    - Service function raises KasbenchError on failure instead of calling sys.exit()
    - Accept typed parameters (working_directory: Path, run_identifier: str, logger)
    - Refactor existing `init_cmd` to call the service function
    - _Requirements: 4.2_

  - [ ] 5.2 Extract `run_build_infrastructure` service
    - Extract core logic from `commands/build_infrastructure.py` into `services/build_infrastructure_service.py`
    - Service function accepts all required parameters and raises KasbenchError on failure
    - Refactor existing `build_infrastructure_cmd` to call the service function
    - _Requirements: 4.3_

  - [ ] 5.3 Extract `run_initialize_runner` service
    - Extract core logic from `commands/initialize_runner.py` into `services/initialize_runner_service.py`
    - Service function accepts typed parameters and raises KasbenchError on failure
    - Refactor existing `initialize_runner_cmd` to call the service function
    - _Requirements: 4.5_

  - [ ] 5.4 Extract `run_benchmark_start` service
    - Extract core logic from `commands/benchmark_start.py` into `services/benchmark_start_service.py`
    - Service function accepts typed parameters and raises KasbenchError on failure
    - Refactor existing `benchmark_start_cmd` to call the service function
    - _Requirements: 4.6, 4.7_

  - [ ] 5.5 Extract `run_benchmark_monitor` service
    - Extract core logic from `commands/benchmark_monitor.py` into `services/benchmark_monitor_service.py`
    - Service function returns the final benchmark status ("success" or "failed")
    - Raise KasbenchError on timeout or API errors
    - Refactor existing `benchmark_monitor_cmd` to call the service function
    - _Requirements: 4.8, 4.9_

  - [ ] 5.6 Extract `run_benchmark_postprocessing` service
    - Extract core logic from `commands/benchmark_postprocessing.py` into `services/benchmark_postprocessing_service.py`
    - Service function raises KasbenchError on failure
    - Refactor existing `benchmark_postprocessing_cmd` to call the service function
    - _Requirements: 4.10_

  - [ ] 5.7 Extract `run_shutdown` service
    - Extract core logic from `commands/shutdown.py` into `services/shutdown_service.py`
    - Service function raises KasbenchError on failure
    - Refactor existing `shutdown_cmd` to call the service function
    - _Requirements: 4.11_

  - [ ] 5.8 Extract `run_destroy_infrastructure` service
    - Extract core logic from `commands/destroy_infrastructure.py` into `services/destroy_infrastructure_service.py`
    - Service function raises KasbenchError on failure
    - Refactor existing `destroy_infrastructure_cmd` to call the service function
    - _Requirements: 4.12, 5.4_

- [ ] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Implement AbortSequence
  - [ ] 7.1 Create `src/kasbench_controller/experiment/abort.py`
    - Implement `AbortSequence.__init__` accepting ExperimentConfig and logger
    - Implement `execute()` with two-tier fallback:
      - Tier 1: Call `run_destroy_infrastructure` service with configured params
      - Tier 2 (on Tier 1 failure): Call `TofuRunner.destroy()` directly from trial's tofu directory with auto-approve
    - Return `AbortResult` indicating success, tier reached, and whether halt is required
    - Skip abort if failure occurred during init step
    - _Requirements: 4.14, 4.15, 5.1, 5.4, 5.5, 5.6, 5.8_

  - [ ]* 7.2 Write unit tests for AbortSequence tier escalation
    - Test Tier 1 success path
    - Test Tier 1 failure → Tier 2 success
    - Test both tiers failing → must_halt=True
    - Test skip when step is "init"
    - _Requirements: 5.4, 5.5, 5.6, 5.8_

- [ ] 8. Implement TrialPipeline
  - [ ] 8.1 Create `src/kasbench_controller/experiment/pipeline.py`
    - Define `TrialPipeline.STEPS` constant with all 10 steps in order
    - Implement `__init__` accepting config, assignment, progress_manager, abort_sequence, logger
    - Implement `execute(start_from_step)` that runs steps sequentially, calling service functions
    - Implement wait step as a 300-second sleep (configurable via ebs_wait for destroy step)
    - Implement upload-logs step using S3Uploader to upload trial output directory
    - On any step failure: record aborted status, invoke abort_sequence, return TrialResult
    - On benchmark-monitor returning "failed" status: treat as success and continue to postprocessing
    - Pass `run_duration + 5` as timeout to benchmark-monitor
    - _Requirements: 4.1–4.15, 5.1, 5.7_

  - [ ]* 8.2 Write property tests for pipeline control flow
    - **Property 9: Monitor timeout is always run_duration + 5**
    - **Property 10: Any step failure triggers abort sequence (except init)**
    - **Validates: Requirements 4.8, 4.14, 5.1, 5.8**

  - [ ]* 8.3 Write property tests for halt-on-error behavior
    - **Property 11: halt-on-error controls whether subsequent trials execute**
    - **Validates: Requirements 5.2, 5.3**

- [ ] 9. Implement ExperimentOrchestrator
  - [ ] 9.1 Create `src/kasbench_controller/experiment/orchestrator.py`
    - Implement `ExperimentOrchestrator.__init__` accepting config and logger
    - Implement `run()` method that:
      1. Generates schedule via TrialScheduler
      2. Loads or creates progress via ProgressManager
      3. Validates parameters if progress exists (exits with mismatch details)
      4. Checks if experiment is already complete
      5. Finds resume point
      6. Iterates through trials, creating TrialPipeline for each
      7. Respects halt_on_error flag (halt all remaining trials on failure)
      8. Returns exit code (0 = all trials complete or experiment already done)
    - _Requirements: 4.1, 5.2, 5.3, 6.1–6.8_

  - [ ] 9.2 Implement experiment logging
    - Create JSON Lines log file at `{working_directory}/benchmarks/{run_identifier}/experiment.log`
    - Emit structured log entries with ISO 8601 UTC timestamp, trial_identifier, autoscaler, step, outcome
    - For failed steps, include error message, stderr, return code, and traceback
    - Exit with error if log file cannot be created/written
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 9.3 Write property tests for log entry structure
    - **Property 14: Log entries contain all required fields**
    - **Validates: Requirements 7.1, 7.4**

- [ ] 10. Implement CLI command integration
  - [ ] 10.1 Create `src/kasbench_controller/commands/run_experiment.py`
    - Define Click command with all parameters from Requirement 1 (1.1–1.24)
    - Implement parameter validation callbacks:
      - run-identifier: 1-128 chars, alphanumeric/hyphens/underscores
      - trial-prefix: 1-64 chars, alphanumeric/hyphens/underscores
      - autoscalers: comma-separated, each in {"hpa", "vpa", "keda", "none"}
      - var: key=value format validation
      - role-params: JSON validation with required keys per role
    - Construct ExperimentConfig from validated parameters
    - Instantiate and call ExperimentOrchestrator.run()
    - Exit with orchestrator's return code
    - _Requirements: 1.1–1.24_

  - [ ] 10.2 Register the command in `src/kasbench_controller/cli.py`
    - Import the run_experiment command module
    - Add `cli.add_command(run_experiment.run_experiment_cmd)`
    - _Requirements: 1.1_

  - [ ]* 10.3 Write property tests for CLI parameter validation
    - **Property 3: Role-params JSON validation**
    - **Property 4: Var-file path resolution**
    - **Validates: Requirements 1.10, 1.18, 1.19**

- [ ] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Update README documentation
  - [ ] 12.1 Add `run-experiment` section to `README.md`
    - Place after the existing single-trial command sections (after "Destroy Infrastructure")
    - Include prose description of multi-trial orchestration purpose
    - Add parameters table with Option, Required, and Description columns for all parameters
    - Document default values for optional parameters
    - Include step-by-step description of trial execution flow
    - Add at least one complete usage example with all required params plus --trial-prefix, --var-file, and --auto-approve
    - Document --rerun-from-failed and --halt-on-error effects on trial execution flow
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [ ] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Service extraction (task 5) is the largest refactor — each existing command becomes a thin wrapper around its service function
- The `experiment/` package contains all new orchestration logic, keeping it isolated from existing command modules
- The project already has `hypothesis>=6.100` in dev dependencies for property-based testing

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "4.1", "5.1"] },
    { "id": 3, "tasks": ["4.2", "4.3", "5.2", "5.3", "5.4"] },
    { "id": 4, "tasks": ["5.5", "5.6", "5.7", "5.8"] },
    { "id": 5, "tasks": ["7.1"] },
    { "id": 6, "tasks": ["7.2", "8.1"] },
    { "id": 7, "tasks": ["8.2", "8.3", "9.1"] },
    { "id": 8, "tasks": ["9.2"] },
    { "id": 9, "tasks": ["9.3", "10.1"] },
    { "id": 10, "tasks": ["10.2", "10.3"] },
    { "id": 11, "tasks": ["12.1"] }
  ]
}
```
