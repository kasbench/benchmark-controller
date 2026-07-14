# Requirements Document

## Introduction

This document specifies requirements for additional CLI flows in the KASBench Controller. The enhancement modifies the existing `build-infrastructure` command to support AWS region selection, S3 artifact uploads, and extended infrastructure output parsing. It also introduces four new CLI flows: `initialize-runner`, `benchmark-start`, `benchmark-monitor`, and `benchmark-postprocessing`, plus a `destroy-infrastructure` flow for teardown. Together these flows orchestrate the full lifecycle of a Kubernetes autoscaler benchmark trial.

## Glossary

- **Controller**: The KASBench Controller CLI application (`kasbench` command)
- **Benchmark_Runner**: A Docker container running on the Benchmark Runner host that exposes an HTTP API on port 8080 for benchmark orchestration
- **Benchmark_Runner_Host**: The EC2 instance (t3.medium) in the public subnet that runs the Benchmark_Runner container
- **Output_Parser**: The `parse_tofu_outputs` function in `output_parser.py` that extracts structured fields from Open Tofu JSON output
- **TofuOutputs**: The dataclass holding parsed infrastructure output values
- **S3_Uploader**: The component responsible for copying local artifact files to the configured S3 bucket
- **SSH_Executor**: The component responsible for executing commands on remote hosts via SSH using the trial's key pair
- **Trial_Context**: A dataclass holding trial-level paths and identifiers (trial_directory, tofu_directory, output_directory)
- **Run_Context**: A dataclass holding run-level paths and identifiers (working_directory, run_identifier)
- **Database_Manager**: The SQLite database manager that records trial state and events
- **Runner_API**: The HTTP REST API exposed by the Benchmark_Runner container on port 8080

## Requirements

### Requirement 1: Build-Infrastructure AWS Region Option

**User Story:** As a benchmark operator, I want to specify the AWS region for my benchmark infrastructure, so that I can run trials in different regions.

#### Acceptance Criteria

1. THE Controller SHALL accept a `--aws-region` option on the `build-infrastructure` command with a default value of `us-east-1`
2. WHEN the `build-infrastructure` command is invoked, THE Controller SHALL persist the aws_region value for use by subsequent flows within the same trial

### Requirement 2: Build-Infrastructure S3 Bucket Option

**User Story:** As a benchmark operator, I want to specify an S3 bucket for artifact storage, so that trial outputs are uploaded to a known location.

#### Acceptance Criteria

1. THE Controller SHALL accept a required `--s3-bucket` option on the `build-infrastructure` command
2. WHEN the `--s3-bucket` option is not provided, THE Controller SHALL exit with a non-zero return code and a descriptive error message
3. WHEN the `build-infrastructure` command is invoked, THE Controller SHALL persist the s3_bucket value for use by subsequent flows within the same trial

### Requirement 3: Build-Infrastructure Run Duration Option

**User Story:** As a benchmark operator, I want to specify the benchmark run duration, so that the Benchmark_Runner knows how long to execute the load test.

#### Acceptance Criteria

1. THE Controller SHALL accept a required `--run-duration` option on the `build-infrastructure` command representing minutes as an integer
2. WHEN the `--run-duration` option is not provided, THE Controller SHALL exit with a non-zero return code and a descriptive error message
3. WHEN the `build-infrastructure` command is invoked, THE Controller SHALL persist the run_duration value for use by subsequent flows within the same trial

### Requirement 4: Build-Infrastructure S3 Artifact Upload

**User Story:** As a benchmark operator, I want infrastructure artifacts uploaded to S3 after the build completes, so that they are available to the Benchmark_Runner and for post-analysis.

#### Acceptance Criteria

1. WHEN the `build-infrastructure` command completes the tofu apply step successfully, THE S3_Uploader SHALL copy `{working_directory}/{run_identifier}/{trial_identifier}/output/tofu_outputs.json` to `{s3_bucket}/{run_identifier}/{trial_identifier}/infrastructure/tofu_outputs.json` in the configured AWS region
2. WHEN the `build-infrastructure` command completes the tofu apply step successfully, THE S3_Uploader SHALL copy `{working_directory}/{run_identifier}/{trial_identifier}/benchmark-infrastructure/artifacts/{trial_identifier}/environment-description.json` to `{s3_bucket}/{run_identifier}/{trial_identifier}/infrastructure/environment-description.json` in the configured AWS region
3. WHEN the `build-infrastructure` command completes the tofu apply step successfully, THE S3_Uploader SHALL copy `{working_directory}/{run_identifier}/{trial_identifier}/benchmark-infrastructure/artifacts/{trial_identifier}/environment-description.md` to `{s3_bucket}/{run_identifier}/{trial_identifier}/infrastructure/environment-description.md` in the configured AWS region
4. IF an S3 upload fails, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message identifying the failed file

### Requirement 5: Enhanced Output Parser

**User Story:** As a benchmark operator, I want the output parser to extract additional infrastructure details, so that the initialize-runner flow has the network addresses it needs.

#### Acceptance Criteria

1. THE Output_Parser SHALL extract `control_plane_private_ip` from `output["control_plane"]["value"]["private_ip"]` and store it as a string in TofuOutputs
2. THE Output_Parser SHALL extract `amd_worker_private_ips` as a list of strings from each element's `private_ip` field in `output["worker_nodes"]["value"]["amd64"]` and store it in TofuOutputs
3. THE Output_Parser SHALL extract `arm_worker_private_ips` as a list of strings from each element's `private_ip` field in `output["worker_nodes"]["value"]["arm64"]` and store it in TofuOutputs
4. THE Output_Parser SHALL extract `globeco_dns` from `output["nlb"]["value"]["dns_name"]` and store it as a string in TofuOutputs
5. THE Output_Parser SHALL extract `globeco_port` from `output["nlb"]["value"]["listeners"]["http"]["port"]` and store it as an integer in TofuOutputs
6. IF any of the new required keys are missing from the tofu output, THEN THE Output_Parser SHALL raise a ValidationError listing all missing keys

### Requirement 6: Initialize-Runner Prerequisite Check

**User Story:** As a benchmark operator, I want the initialize-runner flow to verify that build-infrastructure has been completed, so that I do not attempt initialization on non-existent infrastructure.

#### Acceptance Criteria

1. WHEN the `initialize-runner` command is invoked, THE Controller SHALL verify that the `build-infrastructure` flow has completed for the specified trial
2. IF the `build-infrastructure` flow has not been completed for the specified trial, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 7: Initialize-Runner Docker Pull

**User Story:** As a benchmark operator, I want the runner image pulled onto the Benchmark_Runner_Host, so that the container can be started.

#### Acceptance Criteria

1. WHEN the `initialize-runner` command is invoked, THE SSH_Executor SHALL execute `sudo docker pull kasbench/kasbench-runner:{version}` on the Benchmark_Runner_Host
2. THE Controller SHALL accept a configurable kasbench-runner version with a default value of `0.2.0`
3. IF the docker pull command fails, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 8: Initialize-Runner Docker Network Creation

**User Story:** As a benchmark operator, I want a Docker network created on the Benchmark_Runner_Host, so that the runner container can communicate with other containers.

#### Acceptance Criteria

1. WHEN the `initialize-runner` command is invoked after pulling the image, THE SSH_Executor SHALL execute `sudo docker network create kasbench` on the Benchmark_Runner_Host
2. IF the docker network already exists, THEN THE Controller SHALL continue execution without error

### Requirement 9: Initialize-Runner Container Start

**User Story:** As a benchmark operator, I want the kasbench-runner container started with the correct configuration, so that the Runner_API becomes available.

#### Acceptance Criteria

1. WHEN the docker network is ready, THE SSH_Executor SHALL start the kasbench-runner container on the Benchmark_Runner_Host with the name `kasbench-runner`, network `kasbench`, port mapping `8080:8080`, Docker socket volume mount, and SSH keys volume mount (read-only)
2. IF the container start command fails, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 10: Initialize-Runner Health Check Polling

**User Story:** As a benchmark operator, I want the controller to wait until the Runner_API is available before proceeding, so that subsequent API calls do not fail.

#### Acceptance Criteria

1. WHEN the container has been started, THE Controller SHALL poll `GET http://{benchmark_runner_public_ip}:8080/status` at 1-second intervals until an HTTP 200 response is received
2. THE Controller SHALL use a configurable timeout for the health check polling with a default value of 30 seconds
3. IF the health check polling exceeds the configured timeout without receiving an HTTP 200 response, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 11: Initialize-Runner Initialization Request

**User Story:** As a benchmark operator, I want the Benchmark_Runner initialized with the correct cluster topology and benchmark parameters, so that it can orchestrate the test.

#### Acceptance Criteria

1. WHEN the Runner_API health check succeeds, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/initialize` with a JSON body containing: autoscaler, controlPlaneNode (control_plane_private_ip), amdWorkerNodes (amd_worker_private_ips), armWorkerNodes (arm_worker_private_ips), s3Bucket (s3_bucket), globecoUrl (`http://{globeco_dns}`), globecoPort (globeco_port as integer), runIdentifier (run_identifier), trialIdentifier (trial_identifier), runDurationMinutes (run_duration as integer), skipKubernetesInstall (false), skipManifestInstall (false), forceManifestInstall (true)
2. IF the initialization request returns a non-successful HTTP status code, THEN THE Controller SHALL exit with a non-zero return code and a detailed error message including the response body

### Requirement 12: Initialize-Runner Rollout Wait

**User Story:** As a benchmark operator, I want the controller to wait for the Kubernetes deployment rollout to complete, so that the benchmark starts only when the application is ready.

#### Acceptance Criteria

1. WHEN the initialization request succeeds, THE Controller SHALL poll `POST http://{benchmark_runner_public_ip}:8080/rollout/wait` with JSON body `{"deploymentName": "globeco-confirmation-service", "namespace": "globeco", "timeout": 300}` until an HTTP 200 response is received
2. THE Controller SHALL use a configurable timeout for rollout waiting with a default value of 10 minutes
3. IF the rollout wait exceeds the configured timeout without receiving an HTTP 200 response, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 13: Initialize-Runner Pre-Benchmark Snapshot

**User Story:** As a benchmark operator, I want a pre-benchmark snapshot taken, so that I have a baseline for comparison.

#### Acceptance Criteria

1. WHEN the rollout wait succeeds, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/snapshot` with JSON body `{"phase": "pre"}`
2. IF the snapshot request returns a non-successful HTTP status code, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 14: Initialize-Runner Database Logging

**User Story:** As a benchmark operator, I want each step of the initialize-runner flow recorded in the database, so that I can audit the trial history.

#### Acceptance Criteria

1. WHEN each step of the `initialize-runner` flow completes, THE Database_Manager SHALL record an event with the appropriate event_type and event_message for the current trial

### Requirement 15: Benchmark-Start Flow

**User Story:** As a benchmark operator, I want to start the benchmark with a single command, so that load generation begins against the cluster.

#### Acceptance Criteria

1. WHEN the `benchmark-start` command is invoked, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/start` with an empty JSON body
2. IF the start request returns a non-successful HTTP status code, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message
3. WHEN the start request succeeds, THE Database_Manager SHALL update the trial record with benchmark_start_time and record an appropriate event

### Requirement 16: Benchmark-Monitor Flow Options

**User Story:** As a benchmark operator, I want configurable monitoring parameters, so that I can control how long and how often the benchmark status is checked.

#### Acceptance Criteria

1. THE Controller SHALL accept a `--timeout` option on the `benchmark-monitor` command representing maximum run time in minutes
2. THE Controller SHALL accept an `--interval` option on the `benchmark-monitor` command representing the status check interval in seconds
3. THE Controller SHALL accept a `--verbose` flag on the `benchmark-monitor` command

### Requirement 17: Benchmark-Monitor Status Polling

**User Story:** As a benchmark operator, I want the controller to poll the benchmark status and return when complete, so that I know when results are ready.

#### Acceptance Criteria

1. WHEN the `benchmark-monitor` command is invoked, THE Controller SHALL poll `GET http://{benchmark_runner_public_ip}:8080/status` at the configured interval
2. WHILE the status field in the response is `running` and the timeout has not been reached, THE Controller SHALL continue polling
3. WHILE the `--verbose` flag is set and the status is `running`, THE Controller SHALL print a status message to the console at each interval
4. WHEN the status field is `success` or `failed`, THE Controller SHALL update the trial record with benchmark_end_time and exit with return code 0
5. IF the status polling returns a non-200 HTTP status code, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message
6. IF the configured timeout is reached while the status is still `running`, THEN THE Controller SHALL exit with a non-zero return code and a descriptive timeout message

### Requirement 18: Benchmark-Postprocessing Shutdown

**User Story:** As a benchmark operator, I want a post-benchmark snapshot taken during shutdown, so that I capture the final cluster state.

#### Acceptance Criteria

1. WHEN the `benchmark-postprocessing` command is invoked, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/shutdown`
2. IF the shutdown request returns a non-200 HTTP status code, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 19: Benchmark-Postprocessing Exports

**User Story:** As a benchmark operator, I want all benchmark artifacts exported to S3, so that results are preserved for analysis.

#### Acceptance Criteria

1. WHEN the shutdown step succeeds, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/metrics/export`
2. WHEN the metrics export succeeds, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/metadata/export`
3. WHEN the metadata export succeeds, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/tsdb/export`
4. WHEN the TSDB export succeeds, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/output/export`
5. WHEN the output export succeeds, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/db/export`
6. IF any export request returns a non-200 HTTP status code, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message identifying the failed export step

### Requirement 20: Benchmark-Postprocessing Database Logging

**User Story:** As a benchmark operator, I want each postprocessing step logged to the database, so that I have an audit trail.

#### Acceptance Criteria

1. WHEN each step of the `benchmark-postprocessing` flow completes, THE Database_Manager SHALL record an event with the appropriate event_type and event_message for the current trial

### Requirement 21: Destroy-Infrastructure CLI Options

**User Story:** As a benchmark operator, I want to tear down the benchmark infrastructure with configurable options, so that I can control the destruction process.

#### Acceptance Criteria

1. THE Controller SHALL accept a required `--working-dir` option on the `destroy-infrastructure` command
2. THE Controller SHALL accept a required `--run-identifier` option on the `destroy-infrastructure` command
3. THE Controller SHALL accept a required `--trial-identifier` option on the `destroy-infrastructure` command
4. THE Controller SHALL accept an `--auto-approve` flag on the `destroy-infrastructure` command
5. THE Controller SHALL accept a repeatable `--var-file` option on the `destroy-infrastructure` command
6. THE Controller SHALL accept a repeatable `--var` option on the `destroy-infrastructure` command
7. THE Controller SHALL accept a `--no-apply` flag on the `destroy-infrastructure` command that skips the tofu destroy step

### Requirement 22: Destroy-Infrastructure Runner Shutdown

**User Story:** As a benchmark operator, I want the Benchmark_Runner shut down before infrastructure destruction, so that resources are released cleanly.

#### Acceptance Criteria

1. WHEN the `destroy-infrastructure` command is invoked, THE Controller SHALL send a POST request to `http://{benchmark_runner_public_ip}:8080/shutdown`
2. IF the shutdown request returns a non-200 HTTP status code, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 23: Destroy-Infrastructure EBS Wait

**User Story:** As a benchmark operator, I want the controller to wait for EBS volumes to detach before destroying infrastructure, so that orphaned volumes do not continue accruing cost.

#### Acceptance Criteria

1. WHEN the runner shutdown succeeds, THE Controller SHALL wait for a configurable duration with a default value of 5 minutes before proceeding to the tofu destroy step
2. WHILE waiting, THE Controller SHALL print a progress message to the console every 30 seconds indicating the remaining wait time

### Requirement 24: Destroy-Infrastructure Tofu Destroy

**User Story:** As a benchmark operator, I want the infrastructure destroyed via Open Tofu, so that all AWS resources are cleaned up.

#### Acceptance Criteria

1. WHEN the EBS wait completes and `--no-apply` is not set, THE Controller SHALL navigate to the Open Tofu working directory using the same path logic as `build-infrastructure`
2. WHEN the EBS wait completes and `--no-apply` is not set, THE Controller SHALL execute `tofu destroy` with the provided `--var-file` and `--var` arguments following the conventions established in `build-infrastructure`
3. WHEN `--auto-approve` is set, THE Controller SHALL pass `-auto-approve` to the `tofu destroy` command
4. WHEN `--no-apply` is set, THE Controller SHALL skip the `tofu destroy` step entirely
5. IF the `tofu destroy` command fails, THEN THE Controller SHALL exit with a non-zero return code and a descriptive error message

### Requirement 25: Destroy-Infrastructure Database Logging

**User Story:** As a benchmark operator, I want the destroy-infrastructure flow recorded in the database, so that I have a complete audit trail.

#### Acceptance Criteria

1. WHEN the `destroy-infrastructure` flow begins, THE Database_Manager SHALL record the cleanup_start_time for the trial
2. WHEN the `destroy-infrastructure` flow completes successfully, THE Database_Manager SHALL record the cleanup_end_time for the trial
3. WHEN each step of the `destroy-infrastructure` flow completes, THE Database_Manager SHALL record an event with the appropriate event_type and event_message

### Requirement 26: README Update

**User Story:** As a developer, I want the README updated to document all new and modified CLI commands, so that users know how to operate the full benchmark lifecycle.

#### Acceptance Criteria

1. THE Controller project README SHALL document the `--aws-region`, `--s3-bucket`, and `--run-duration` options for `build-infrastructure`
2. THE Controller project README SHALL document the `initialize-runner` command and its options
3. THE Controller project README SHALL document the `benchmark-start` command
4. THE Controller project README SHALL document the `benchmark-monitor` command and its options
5. THE Controller project README SHALL document the `benchmark-postprocessing` command
6. THE Controller project README SHALL document the `destroy-infrastructure` command and its options
