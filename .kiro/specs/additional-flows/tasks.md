# Implementation Plan: Additional Flows

## Overview

This plan implements the full benchmark lifecycle orchestration for KASBench Controller. Tasks are ordered: foundational data models and exceptions first, then building-block modules (S3, SSH, Runner API), then commands in lifecycle order, and finally integration wiring. Each task builds incrementally on previous steps.

## Tasks

- [ ] 1. Extend foundational modules (models, exceptions, database)
  - [ ] 1.1 Extend `models.py` with `TrialConfig` dataclass and enhanced `TofuOutputs`
    - Add `control_plane_private_ip: str | None`, `amd_worker_private_ips: list[str]`, `arm_worker_private_ips: list[str]`, `globeco_dns: str | None`, `globeco_port: int | None` fields to `TofuOutputs`
    - Add new `TrialConfig` dataclass with fields: `aws_region`, `s3_bucket`, `run_duration`, `benchmark_runner_public_ip`, `ssh_key_pair_name`, `control_plane_private_ip`, `amd_worker_private_ips`, `arm_worker_private_ips`, `globeco_dns`, `globeco_port`
    - Add `load_trial_config(trial_ctx: TrialContext) -> TrialConfig` helper function that reads `trial_config.json` from `trial_ctx.output_directory` and raises `KasbenchError` if not found
    - Add `save_trial_config(trial_ctx: TrialContext, config: TrialConfig) -> None` helper function that writes `trial_config.json`
    - _Requirements: 1.2, 2.3, 3.3, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1_

  - [ ] 1.2 Add new exception types to `exceptions.py`
    - Add `S3UploadError(KasbenchError)` with `file_path: str` and `stderr: str` attributes
    - Add `SSHError(KasbenchError)` with `command: str`, `stderr: str`, `return_code: int` attributes
    - Add `RunnerAPIError(KasbenchError)` with `endpoint: str`, `status_code: int | None`, `response_body: str` attributes
    - Add `TimeoutError(KasbenchError)` with `operation: str`, `elapsed: float` attributes
    - _Requirements: 4.4, 7.3, 9.2, 10.3, 11.2, 19.6_

  - [ ] 1.3 Add new methods to `DatabaseManager` in `database.py`
    - Add `update_trial_status(trial_id: int, status: str) -> None` — validates against VALID_STATUSES
    - Add `record_benchmark_start_time(trial_id: int) -> None`
    - Add `record_benchmark_end_time(trial_id: int) -> None`
    - Add `record_infra_end_time(trial_id: int) -> None`
    - Add `record_cleanup_start_time(trial_id: int) -> None`
    - Add `record_cleanup_end_time(trial_id: int) -> None`
    - Add `insert_event(trial_id: int, event_type: str, event_message: str, event_request: str | None = None) -> int`
    - Add `get_trial_by_identifiers(run_identifier: str, trial_identifier: str) -> dict | None`
    - _Requirements: 14.1, 15.3, 17.4, 20.1, 25.1, 25.2, 25.3_

  - [ ]* 1.4 Write unit tests for new database methods
    - Test `update_trial_status` with valid and invalid statuses
    - Test `record_benchmark_start_time`, `record_benchmark_end_time`, `record_cleanup_start_time`, `record_cleanup_end_time`
    - Test `insert_event` and verify event retrieval
    - Test `get_trial_by_identifiers` with existing and non-existing trials
    - _Requirements: 14.1, 15.3, 25.1, 25.2, 25.3_

- [ ] 2. Enhance output parser
  - [ ] 2.1 Extend `parse_tofu_outputs` in `output_parser.py` to extract new fields
    - Extract `control_plane_private_ip` from `output["control_plane"]["value"]["private_ip"]`
    - Extract `amd_worker_private_ips` from `output["worker_nodes"]["value"]["amd64"]` (list of `private_ip` fields)
    - Extract `arm_worker_private_ips` from `output["worker_nodes"]["value"]["arm64"]` (list of `private_ip` fields)
    - Extract `globeco_dns` from `output["nlb"]["value"]["dns_name"]`
    - Extract `globeco_port` from `output["nlb"]["value"]["listeners"]["http"]["port"]` (as integer)
    - Aggregate all missing keys and raise `ValidationError` listing them all
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 2.2 Write property test for output parser field extraction
    - **Property 2: Output parser extracts all fields correctly**
    - Generate random valid tofu output JSON with randomized IPs, DNS names, ports, and worker lists
    - Verify all fields extracted match source values, worker IP list order is preserved, `globeco_port` is integer
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**

  - [ ]* 2.3 Write property test for output parser missing key reporting
    - **Property 3: Output parser reports all missing keys**
    - Generate random subsets of required keys to omit from tofu output
    - Verify `ValidationError` message contains exactly the missing key names and no others
    - **Validates: Requirements 5.6**

- [ ] 3. Implement S3 uploader module
  - [ ] 3.1 Create `src/kasbench_controller/s3_uploader.py`
    - Implement `S3UploadResult` dataclass with `success`, `source_path`, `destination_uri`, `stderr` fields
    - Implement `S3Uploader` class with `__init__(bucket, region, dry_run)`, `upload_file(local_path, s3_key)`, and `upload_trial_artifacts(trial_ctx, run_identifier, trial_identifier)` methods
    - Use `subprocess.run()` calling `aws s3 cp` with `--region` flag
    - Support dry-run mode with logging
    - Raise `S3UploadError` on failure
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 3.2 Write property test for S3 path construction
    - **Property 1: S3 path construction correctness**
    - Generate random alphanumeric `run_identifier` and `trial_identifier` strings
    - Verify destination key format is `{run_identifier}/{trial_identifier}/infrastructure/{filename}` for each artifact type
    - Verify source paths resolve correctly within trial directory structure
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [ ]* 3.3 Write unit tests for S3Uploader
    - Test `upload_file` subprocess command construction (mocked subprocess)
    - Test `upload_trial_artifacts` uploads all three expected files
    - Test dry-run mode logs without executing
    - Test `S3UploadError` raised on subprocess failure
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

- [ ] 4. Implement SSH executor module
  - [ ] 4.1 Create `src/kasbench_controller/ssh_executor.py`
    - Implement `SSHResult` dataclass with `return_code`, `stdout`, `stderr`, `success` fields
    - Implement `SSHExecutor` class with `__init__(host, key_path, user="ubuntu", dry_run=False)` and `execute(command, timeout=120)` method
    - Build SSH command: `ssh -i {key_path} -o StrictHostKeyChecking=no -o ConnectTimeout=10 {user}@{host} "{command}"`
    - Support dry-run mode with logging
    - Raise `SSHError` on non-zero exit code
    - _Requirements: 7.1, 7.3, 8.1, 9.1, 9.2_

  - [ ]* 4.2 Write unit tests for SSHExecutor
    - Test command construction with various inputs (mocked subprocess)
    - Test dry-run mode logging
    - Test `SSHError` raised on non-zero return code
    - _Requirements: 7.1, 8.1, 9.1_

- [ ] 5. Implement Runner API client module
  - [ ] 5.1 Create `src/kasbench_controller/runner_api.py`
    - Implement `RunnerAPIClient` class with `__init__(base_url, timeout=30.0)` using `httpx.Client`
    - Implement methods: `health_check()`, `initialize(config)`, `rollout_wait(deployment_name, namespace, timeout)`, `snapshot(phase)`, `start()`, `status()`, `shutdown()`, `export(export_type)`
    - Each method raises `RunnerAPIError` on non-successful HTTP responses with endpoint, status code, and response body details
    - _Requirements: 10.1, 11.1, 12.1, 13.1, 15.1, 17.1, 18.1, 19.1-19.5, 22.1_

  - [ ]* 5.2 Write property test for initialize request body construction
    - **Property 4: Initialize request body construction correctness**
    - Generate random `TrialConfig` instances with randomized IPs, ports, identifiers
    - Verify constructed JSON body contains all required fields with correct values
    - Verify `globecoUrl` formatted as `http://{globeco_dns}` and `globecoPort` as integer
    - **Validates: Requirements 11.1**

  - [ ]* 5.3 Write property test for export error identification
    - **Property 5: Export error identifies failed step**
    - For each export type (metrics, metadata, tsdb, output, db), simulate non-200 response
    - Verify error message contains the failed export type name and no others
    - **Validates: Requirements 19.6**

  - [ ]* 5.4 Write unit tests for RunnerAPIClient
    - Test each method constructs correct request (mocked with `respx`)
    - Test `RunnerAPIError` raised on non-200 responses with proper details
    - Test `health_check` returns True/False appropriately
    - _Requirements: 10.1, 11.1, 15.1, 18.1_

- [ ] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Add `destroy` method to `TofuRunner` in `tofu.py`
  - [ ] 7.1 Implement `TofuRunner.destroy` method
    - Add `destroy(var_files, variables, run_id, auto_approve)` method following the `apply` pattern
    - Build command: `tofu destroy` + var args + optional `-auto-approve`
    - Support dry-run mode
    - Raise `TofuError` on failure
    - _Requirements: 24.1, 24.2, 24.3, 24.5_

  - [ ]* 7.2 Write unit tests for `TofuRunner.destroy`
    - Test argument assembly matches expected format (mocked subprocess)
    - Test `-auto-approve` flag passed when set
    - Test dry-run mode
    - _Requirements: 24.1, 24.2, 24.3_

- [ ] 8. Modify `build-infrastructure` command with new options and S3 upload
  - [ ] 8.1 Add `--aws-region`, `--s3-bucket`, `--run-duration` options to `build_infrastructure_cmd`
    - Add `--aws-region` option with default `us-east-1`
    - Add `--s3-bucket` as required option
    - Add `--run-duration` as required integer option
    - Wire new options into the command logic
    - _Requirements: 1.1, 2.1, 2.2, 3.1, 3.2_

  - [ ] 8.2 Add S3 upload step and `trial_config.json` write to `build-infrastructure`
    - After tofu apply + output parsing, instantiate `S3Uploader` and call `upload_trial_artifacts`
    - After S3 upload, build `TrialConfig` from parsed outputs and new options, then call `save_trial_config`
    - Record `infra_end_time` in the database
    - Update dry-run mode to log the new steps
    - _Requirements: 1.2, 2.3, 3.3, 4.1, 4.2, 4.3, 4.4_

- [ ] 9. Implement `initialize-runner` command
  - [ ] 9.1 Create `src/kasbench_controller/commands/initialize_runner.py`
    - Accept `--working-directory`, `--run-identifier`, `--trial-identifier`, `--runner-version` (default `0.2.0`), `--health-timeout` (default 30), `--rollout-timeout` (default 600)
    - Load `trial_config.json` (prerequisite check — exits with error if missing)
    - Look up trial in database via `get_trial_by_identifiers`
    - SSH steps: docker pull, docker network create (ignore "already exists" error), docker run with name/network/port/volumes
    - Health check: poll `GET /status` at 1s intervals until 200 or timeout
    - Initialize: POST `/initialize` with constructed body from `TrialConfig`
    - Rollout wait: POST `/rollout/wait` with polling until 200 or timeout
    - Snapshot: POST `/snapshot` with `{"phase": "pre"}`
    - Record events in database at each step
    - Support dry-run mode
    - _Requirements: 6.1, 6.2, 7.1, 7.2, 7.3, 8.1, 8.2, 9.1, 9.2, 10.1, 10.2, 10.3, 11.1, 11.2, 12.1, 12.2, 12.3, 13.1, 13.2, 14.1_

  - [ ] 9.2 Register `initialize-runner` command in `cli.py` and `commands/__init__.py`
    - Import and add command to CLI group
    - _Requirements: 6.1_

- [ ] 10. Implement `benchmark-start` command
  - [ ] 10.1 Create `src/kasbench_controller/commands/benchmark_start.py`
    - Accept `--working-directory`, `--run-identifier`, `--trial-identifier`
    - Load `trial_config.json`
    - Look up trial in database
    - POST `/start` with empty JSON body
    - Record `benchmark_start_time` in database
    - Insert event for benchmark start
    - Support dry-run mode
    - _Requirements: 15.1, 15.2, 15.3_

  - [ ] 10.2 Register `benchmark-start` command in `cli.py` and `commands/__init__.py`
    - _Requirements: 15.1_

- [ ] 11. Implement `benchmark-monitor` command
  - [ ] 11.1 Create `src/kasbench_controller/commands/benchmark_monitor.py`
    - Accept `--working-directory`, `--run-identifier`, `--trial-identifier`, `--timeout` (minutes), `--interval` (seconds, default 30), `--verbose`
    - Load `trial_config.json`
    - Look up trial in database
    - Poll `GET /status` at configured interval
    - While status is `running` and timeout not reached, continue; if `--verbose`, print status message
    - On `success` or `failed`: record `benchmark_end_time`, exit 0
    - On non-200 response: exit 1 with error
    - On timeout: exit 1 with timeout message
    - Support dry-run mode
    - _Requirements: 16.1, 16.2, 16.3, 17.1, 17.2, 17.3, 17.4, 17.5, 17.6_

  - [ ] 11.2 Register `benchmark-monitor` command in `cli.py` and `commands/__init__.py`
    - _Requirements: 16.1_

- [ ] 12. Implement `benchmark-postprocessing` command
  - [ ] 12.1 Create `src/kasbench_controller/commands/benchmark_postprocessing.py`
    - Accept `--working-directory`, `--run-identifier`, `--trial-identifier`
    - Load `trial_config.json`
    - Look up trial in database
    - POST `/shutdown` — exit 1 on non-200
    - Sequential exports: POST `/metrics/export`, `/metadata/export`, `/tsdb/export`, `/output/export`, `/db/export`
    - Exit 1 with descriptive error identifying the failed step if any export fails
    - Record events for each step
    - Support dry-run mode
    - _Requirements: 18.1, 18.2, 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 20.1_

  - [ ] 12.2 Register `benchmark-postprocessing` command in `cli.py` and `commands/__init__.py`
    - _Requirements: 18.1_

- [ ] 13. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 14. Implement `destroy-infrastructure` command
  - [ ] 14.1 Create `src/kasbench_controller/commands/destroy_infrastructure.py`
    - Accept `--working-directory`, `--run-identifier`, `--trial-identifier`, `--auto-approve`, `--var-file` (multiple), `--var` (multiple), `--no-apply`, `--ebs-wait` (default 300 seconds)
    - Load `trial_config.json`
    - Look up trial in database, record `cleanup_start_time`
    - POST `/shutdown` to runner — exit 1 on non-200
    - EBS wait: sleep for configured duration, printing progress every 30 seconds
    - If not `--no-apply`: navigate to tofu directory, run `TofuRunner.destroy` with var-files/vars/auto-approve
    - Record `cleanup_end_time`
    - Record events for each step
    - Support dry-run mode
    - _Requirements: 21.1-21.7, 22.1, 22.2, 23.1, 23.2, 24.1, 24.2, 24.3, 24.4, 24.5, 25.1, 25.2, 25.3_

  - [ ] 14.2 Register `destroy-infrastructure` command in `cli.py` and `commands/__init__.py`
    - _Requirements: 21.1_

- [ ] 15. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ]* 16. Write integration tests for lifecycle flows
  - [ ]* 16.1 Write integration test for `build-infrastructure` with new options and S3 upload
    - Mock subprocess for tofu and aws CLI, verify S3 upload and `trial_config.json` written
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 4.2, 4.3_

  - [ ]* 16.2 Write integration test for `initialize-runner` full flow
    - Mock SSH subprocess and httpx responses, verify all steps execute in order, events recorded
    - _Requirements: 6.1, 7.1, 8.1, 9.1, 10.1, 11.1, 12.1, 13.1, 14.1_

  - [ ]* 16.3 Write integration test for benchmark lifecycle (start → monitor → postprocessing)
    - Mock httpx responses, verify status polling, database timestamps, and export sequence
    - _Requirements: 15.1, 15.3, 17.1, 17.4, 18.1, 19.1-19.5, 20.1_

  - [ ]* 16.4 Write integration test for `destroy-infrastructure` full flow
    - Mock httpx and subprocess, verify shutdown, EBS wait, tofu destroy, database timestamps
    - _Requirements: 22.1, 23.1, 24.1, 25.1, 25.2, 25.3_

- [ ] 17. Update README with new command documentation
  - [ ] 17.1 Update `README.md` to document all new and modified CLI commands
    - Document `--aws-region`, `--s3-bucket`, `--run-duration` options for `build-infrastructure`
    - Document `initialize-runner` command and its options
    - Document `benchmark-start` command
    - Document `benchmark-monitor` command and its options (`--timeout`, `--interval`, `--verbose`)
    - Document `benchmark-postprocessing` command
    - Document `destroy-infrastructure` command and its options
    - _Requirements: 26.1, 26.2, 26.3, 26.4, 26.5, 26.6_

- [ ] 18. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties defined in the design
- Unit tests validate specific examples and edge cases
- The project uses Python 3.13+, Click, structlog, httpx, pytest, and Hypothesis
- All new modules follow existing patterns (subprocess wrappers, structured results, dry-run support)
- `trial_config.json` serves as the data contract between `build-infrastructure` and all subsequent commands

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1"] },
    { "id": 2, "tasks": ["1.4", "2.2", "2.3", "3.1", "4.1", "5.1", "7.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "4.2", "5.2", "5.3", "5.4", "7.2"] },
    { "id": 4, "tasks": ["8.1"] },
    { "id": 5, "tasks": ["8.2"] },
    { "id": 6, "tasks": ["9.1", "10.1", "11.1", "12.1", "14.1"] },
    { "id": 7, "tasks": ["9.2", "10.2", "11.2", "12.2", "14.2"] },
    { "id": 8, "tasks": ["16.1", "16.2", "16.3", "16.4"] },
    { "id": 9, "tasks": ["17.1"] }
  ]
}
```
