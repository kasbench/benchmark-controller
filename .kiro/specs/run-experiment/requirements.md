# Requirements Document

## Introduction

The `run-experiment` command orchestrates a full multi-trial benchmark experiment. It automates the lifecycle of multiple trials (init, build-infrastructure, initialize-runner, benchmark-start, benchmark-monitor, benchmark-postprocessing, shutdown, destroy-infrastructure) across a randomized schedule of autoscaler selections. The command supports resumption from failure, progress persistence to S3, and configurable error handling.

## Glossary

- **Experiment**: A complete benchmark run consisting of multiple trials across one or more autoscaler configurations.
- **Trial**: A single iteration of the benchmark lifecycle from infrastructure provisioning through teardown.
- **Trial_Identifier**: A string formed by concatenating the trial prefix with a zero-padded four-digit sequential number (e.g., "trial0001").
- **Autoscaler**: A Kubernetes autoscaling strategy. Valid values are "hpa", "vpa", "keda", and "none".
- **Run_Identifier**: A unique string identifying the experiment.
- **Progress_File**: A JSON file stored in S3 at `{s3-bucket}/{run-identifier}/experiment-progress.json` that records parameter values and per-trial completion state.
- **Experiment_Orchestrator**: The `run-experiment` CLI command implementation.
- **Trial_Step**: One of the sequential operations within a trial (init, build-infrastructure, wait, initialize-runner, benchmark-start, benchmark-monitor, benchmark-postprocessing, shutdown, destroy-infrastructure, upload-logs).
- **Abort_Sequence**: The cleanup procedure executed when a trial step fails, consisting of destroy-infrastructure followed by a fallback direct tofu destroy.

## Requirements

### Requirement 1: CLI Parameter Parsing

**User Story:** As a researcher, I want to configure the experiment through CLI parameters, so that I can control trial count, autoscaler selection, and infrastructure settings from a single command invocation.

#### Acceptance Criteria

1. THE Experiment_Orchestrator SHALL accept `--run-identifier` as a required string parameter with a minimum length of 1 character and a maximum length of 128 characters, restricted to alphanumeric characters, hyphens, and underscores.
2. THE Experiment_Orchestrator SHALL accept `--trial-prefix` as an optional string parameter with a default value of "trial", a minimum length of 1 character, and a maximum length of 64 characters, restricted to alphanumeric characters, hyphens, and underscores.
3. THE Experiment_Orchestrator SHALL accept `--autoscalers` as a required comma-separated string parameter restricted to values "hpa", "vpa", "keda", and "none", with at least one value provided.
4. IF `--autoscalers` contains a value not in the set {"hpa", "vpa", "keda", "none"}, THEN THE Experiment_Orchestrator SHALL exit with a non-zero status and an error message indicating the invalid autoscaler value.
5. THE Experiment_Orchestrator SHALL accept `--trials-per-autoscaler` as a required integer parameter with a minimum value of 1 and a maximum value of 9999.
6. THE Experiment_Orchestrator SHALL accept `--run-duration` as a required integer parameter specifying benchmark duration in minutes with a minimum value of 1.
7. THE Experiment_Orchestrator SHALL accept `--working-directory` as a required path parameter.
8. THE Experiment_Orchestrator SHALL accept `--s3-bucket` as a required string parameter.
9. THE Experiment_Orchestrator SHALL accept `--aws-region` as an optional string parameter with a default value of "us-east-1".
10. THE Experiment_Orchestrator SHALL accept `--var-file` as a repeatable optional string parameter, where filenames without path separators resolve to the `environments/` directory.
11. THE Experiment_Orchestrator SHALL accept `--var` as a repeatable optional string parameter in `key=value` format.
12. IF `--var` contains a value that does not include exactly one `=` separating a non-empty key from a value, THEN THE Experiment_Orchestrator SHALL exit with a non-zero status and an error message indicating the malformed variable assignment.
13. THE Experiment_Orchestrator SHALL accept `--auto-approve` as an optional boolean flag defaulting to False.
14. THE Experiment_Orchestrator SHALL accept `--runner-version` as an optional string parameter with a default value of "0.2.6".
15. THE Experiment_Orchestrator SHALL accept `--health-timeout` as an optional integer parameter with a default value of 30 seconds and a minimum value of 1.
16. THE Experiment_Orchestrator SHALL accept `--rollout-timeout` as an optional integer parameter with a default value of 600 seconds and a minimum value of 1.
17. THE Experiment_Orchestrator SHALL accept `--cluster-cidr-range` as an optional string parameter with a default value of "10.244.0.0/16".
18. THE Experiment_Orchestrator SHALL accept `--role-params` as an optional JSON string parameter where each role object must contain the keys `baseLoadIntensity`, `baseDelayPercentage`, and `spawnRate`.
19. IF `--role-params` contains invalid JSON or a role object missing any of the required keys (`baseLoadIntensity`, `baseDelayPercentage`, `spawnRate`), THEN THE Experiment_Orchestrator SHALL exit with a non-zero status and an error message indicating the validation failure.
20. THE Experiment_Orchestrator SHALL accept `--random-seed` as an optional integer parameter.
21. THE Experiment_Orchestrator SHALL accept `--ebs-wait` as an optional integer parameter with a default value of 300 seconds and a minimum value of 1.
22. THE Experiment_Orchestrator SHALL accept `--rerun-from-failed` as an optional boolean flag defaulting to False.
23. THE Experiment_Orchestrator SHALL accept `--halt-on-error` as an optional boolean flag defaulting to False.
24. WHEN `--autoscalers` contains duplicate values, THE Experiment_Orchestrator SHALL treat each occurrence as a separate autoscaler entry in the scheduling list.

### Requirement 2: Trial Identifier Generation

**User Story:** As a researcher, I want each trial to have a unique sequential identifier, so that I can trace results back to specific trial executions.

#### Acceptance Criteria

1. THE Experiment_Orchestrator SHALL generate trial identifiers by concatenating the `--trial-prefix` value (default: "trial") with a zero-padded four-digit sequential number starting at 0001 (e.g., "trial0001", "trial0002").
2. THE Experiment_Orchestrator SHALL assign trial identifiers sequentially without gaps, incrementing by one for each trial regardless of whether the trial succeeds or fails, such that no identifier is reused or skipped.
3. THE Experiment_Orchestrator SHALL produce a total number of trials equal to the count of entries in `--autoscalers` multiplied by `--trials-per-autoscaler`, where `--trials-per-autoscaler` is an integer between 1 and 2499 inclusive.
4. IF the computed total number of trials exceeds 9999, THEN THE Experiment_Orchestrator SHALL reject the experiment with an error message indicating that the trial count exceeds the four-digit identifier limit.

### Requirement 3: Autoscaler Randomization

**User Story:** As a researcher, I want autoscaler assignment to trials to be randomized, so that temporal ordering effects are mitigated in the experiment.

#### Acceptance Criteria

1. THE Experiment_Orchestrator SHALL produce a total trial count equal to the number of entries in `--autoscalers` (including duplicates) multiplied by `--trials-per-autoscaler`, and SHALL assign autoscalers to trials by uniformly random selection from the pool of autoscaler entries that have been assigned fewer than `--trials-per-autoscaler` times.
2. WHEN `--random-seed` is supplied, THE Experiment_Orchestrator SHALL seed the random number generator with the provided integer value before generating the trial sequence, such that identical values of `--random-seed`, `--autoscalers`, and `--trials-per-autoscaler` always produce the same autoscaler-to-trial assignment order.
3. IF `--random-seed` is not supplied, THEN THE Experiment_Orchestrator SHALL seed the random number generator with a non-deterministic source and log the effective seed used.
4. THE Experiment_Orchestrator SHALL treat each entry in the `--autoscalers` list independently, including duplicate entries, and SHALL select each entry exactly `--trials-per-autoscaler` times across the experiment.
5. IF the randomly selected autoscaler entry has already been assigned `--trials-per-autoscaler` times, THEN THE Experiment_Orchestrator SHALL exclude that entry from the pool and select again from the remaining entries that have not yet reached `--trials-per-autoscaler` assignments.

### Requirement 4: Trial Execution Pipeline

**User Story:** As a researcher, I want each trial to progress through the complete benchmark lifecycle automatically, so that the experiment runs without manual intervention.

#### Acceptance Criteria

1. THE Experiment_Orchestrator SHALL execute the following steps in order for each trial: init, build-infrastructure, wait, initialize-runner, benchmark-start, benchmark-monitor, benchmark-postprocessing, shutdown, destroy-infrastructure, upload-logs.
2. WHEN executing the init step, THE Experiment_Orchestrator SHALL invoke the equivalent of `kasbench init` with the configured working-directory and run-identifier.
3. WHEN executing the build-infrastructure step, THE Experiment_Orchestrator SHALL invoke the equivalent of `kasbench build-infrastructure` passing working-directory, run-identifier, trial-identifier, the assigned autoscaler, aws-region, s3-bucket, run-duration, auto-approve, var-file, and var parameters.
4. WHEN executing the wait step, THE Experiment_Orchestrator SHALL pause execution for 300 seconds (5 minutes) before proceeding to the next step.
5. WHEN executing the initialize-runner step, THE Experiment_Orchestrator SHALL invoke the equivalent of `kasbench initialize-runner` passing working-directory, run-identifier, trial-identifier, runner-version, health-timeout, rollout-timeout, and cluster-cidr-range.
6. WHEN executing the benchmark-start step, THE Experiment_Orchestrator SHALL invoke the equivalent of `kasbench benchmark-start` passing working-directory, run-identifier, and trial-identifier.
7. IF `--role-params` is supplied, THEN THE Experiment_Orchestrator SHALL pass role-params to the benchmark-start step.
8. WHEN executing the benchmark-monitor step, THE Experiment_Orchestrator SHALL invoke the equivalent of `kasbench benchmark-monitor` with a timeout value equal to `--run-duration` plus 5 minutes.
9. WHEN the benchmark-monitor step completes with a benchmark status of "success" or "failed", THE Experiment_Orchestrator SHALL proceed to the benchmark-postprocessing step without aborting the trial.
10. WHEN executing the benchmark-postprocessing step, THE Experiment_Orchestrator SHALL invoke the equivalent of `kasbench benchmark-postprocessing` passing working-directory, run-identifier, and trial-identifier.
11. WHEN executing the shutdown step, THE Experiment_Orchestrator SHALL invoke the equivalent of `kasbench shutdown` passing working-directory, run-identifier, and trial-identifier.
12. WHEN executing the destroy-infrastructure step, THE Experiment_Orchestrator SHALL invoke the equivalent of `kasbench destroy-infrastructure` passing working-directory, run-identifier, trial-identifier, auto-approve, var-file, var, and ebs-wait parameters.
13. WHEN executing the upload-logs step, THE Experiment_Orchestrator SHALL upload the contents of the trial output directory to S3 at the path `{s3-bucket}/{run-identifier}/{trial-identifier}/benchmark-results`.
14. IF any step returns a non-zero exit code or raises an exception, THEN THE Experiment_Orchestrator SHALL abort the current trial, attempt cleanup by invoking destroy-infrastructure, and proceed to the next trial without reusing the trial identifier.
15. IF the destroy-infrastructure cleanup attempt fails during abort, THEN THE Experiment_Orchestrator SHALL attempt a direct `tofu destroy` from the trial's benchmark-infrastructure directory with the configured var-file and var parameters and auto-approve enabled, and if that also fails, THE Experiment_Orchestrator SHALL halt processing with an error message indicating infrastructure could not be destroyed.

### Requirement 5: Error Handling and Abort Sequence

**User Story:** As a researcher, I want failed trials to be cleaned up properly and the experiment to continue with remaining trials, so that infrastructure resources are not leaked.

#### Acceptance Criteria

1. WHEN any Trial_Step (init, build-infrastructure, initialize-runner, benchmark-start, benchmark-monitor, benchmark-postprocessing, shutdown, or destroy-infrastructure) returns a non-zero exit code, THE Experiment_Orchestrator SHALL record the trial status as "aborted" in the database and initiate the Abort_Sequence for the current trial.
2. WHEN `--halt-on-error` is True AND a Trial_Step fails, THE Experiment_Orchestrator SHALL log the error details (step name, exit code, and stderr output) to stdout and the run log file, skip all remaining trials, and exit with a non-zero exit code.
3. WHEN `--halt-on-error` is False AND a Trial_Step fails, THE Experiment_Orchestrator SHALL complete the Abort_Sequence for the current trial and proceed to the next scheduled trial.
4. WHEN executing the Abort_Sequence, THE Experiment_Orchestrator SHALL first attempt to invoke `kasbench destroy-infrastructure` (or its programmatic equivalent) passing the same `--working-directory`, `--run-identifier`, `--trial-identifier`, `--var-file`, `--var`, `--ebs-wait`, and `--auto-approve` parameters that were configured for the experiment.
5. IF the destroy-infrastructure invocation in the Abort_Sequence returns a non-zero exit code, THEN THE Experiment_Orchestrator SHALL attempt a direct `tofu destroy` command with the configured `--var-file` and `--var` parameters and `-auto-approve` flag, executed from the `/{working-directory}/benchmarks/{run-identifier}/{trial-identifier}/benchmark-infrastructure` directory.
6. IF the direct tofu destroy in the Abort_Sequence returns a non-zero exit code, THEN THE Experiment_Orchestrator SHALL skip all remaining trials and exit with a non-zero exit code, including an error message indicating that infrastructure for the failed trial could not be destroyed and that subsequent trials cannot proceed.
7. WHEN the benchmark-monitor step exits with code 0 and the benchmark status is "failed", THE Experiment_Orchestrator SHALL treat the trial as completed without error and proceed to the postprocessing step.
8. WHEN a Trial_Step fails before infrastructure has been created (during the init step), THE Experiment_Orchestrator SHALL skip the Abort_Sequence for that trial and proceed directly to the next trial or halt per the `--halt-on-error` setting.

### Requirement 6: Progress Persistence and Resumption

**User Story:** As a researcher, I want the experiment to persist progress to S3 and resume from the point of failure, so that long-running experiments can survive transient failures.

#### Acceptance Criteria

1. WHEN a new experiment starts, THE Experiment_Orchestrator SHALL create a Progress_File at `{s3-bucket}/{run-identifier}/experiment-progress.json` containing all invocation parameter values, the ordered list of trial identifiers with their assigned autoscalers, and an empty trial results array.
2. WHEN a trial step completes successfully or fails, THE Experiment_Orchestrator SHALL update the Progress_File with the trial identifier, the step name that completed or failed, a status value of "success" or "failed", and a timestamp, within 30 seconds of step completion.
3. WHEN starting an experiment, THE Experiment_Orchestrator SHALL check S3 for an existing Progress_File at `{s3-bucket}/{run-identifier}/experiment-progress.json`.
4. WHEN a Progress_File exists AND `--rerun-from-failed` is False, THE Experiment_Orchestrator SHALL compare all stored invocation parameters against the current invocation parameters, excluding `--halt-on-error` and `--rerun-from-failed`.
5. IF the stored parameters do not match the current invocation parameters, THEN THE Experiment_Orchestrator SHALL exit with a non-zero status and a message indicating which parameters differ between the stored and current invocations.
6. WHEN a Progress_File exists AND all trials in the Progress_File have a status of "complete" (all steps recorded as "success"), THE Experiment_Orchestrator SHALL exit with a zero status and a message indicating the experiment is already complete.
7. WHEN a Progress_File exists AND parameters match AND `--rerun-from-failed` is False, THE Experiment_Orchestrator SHALL resume processing from the first trial whose step records do not show all steps as "success".
8. WHEN `--rerun-from-failed` is True, THE Experiment_Orchestrator SHALL skip trials whose final step is recorded as "success" and begin execution at the first step recorded as "failed" or not yet recorded within the first non-complete trial.
9. THE Experiment_Orchestrator SHALL track the following steps per trial in the Progress_File: init, build-infrastructure, wait, initialize-runner, benchmark-start, benchmark-monitor, benchmark-postprocessing, shutdown, destroy-infrastructure, and upload-logs.
10. IF the Progress_File cannot be read or contains malformed JSON, THEN THE Experiment_Orchestrator SHALL exit with a non-zero status and a message indicating the progress file is corrupted.
11. IF an S3 upload of the Progress_File fails, THEN THE Experiment_Orchestrator SHALL retry the upload up to 3 times with a 5-second delay between attempts before reporting the failure and continuing trial execution.

### Requirement 7: Logging and Output

**User Story:** As a researcher, I want comprehensive logging to both stdout and a file, so that I can monitor experiment progress in real time and review detailed logs later.

#### Acceptance Criteria

1. WHEN a trial starts, a step completes, a step fails, or a trial completes, THE Experiment_Orchestrator SHALL output a structured JSON log entry to stdout containing the trial identifier, assigned autoscaler, current step name, and outcome status.
2. THE Experiment_Orchestrator SHALL write all structured log entries to a JSON Lines file located at `{working-directory}/benchmarks/{run-identifier}/experiment.log`, appending entries without overwriting previous content.
3. WHEN uploading trial logs to S3, THE Experiment_Orchestrator SHALL include for each failed step the error message, captured stderr output, command return code, and Python stack trace if available.
4. THE Experiment_Orchestrator SHALL include in every log entry an ISO 8601 UTC timestamp, the trial identifier, assigned autoscaler, and current step name.
5. IF the log file cannot be created or written to, THEN THE Experiment_Orchestrator SHALL exit with a non-zero exit code and output an error message to stderr indicating the file path and the OS-level failure reason.

### Requirement 8: README Documentation

**User Story:** As a developer, I want the README.md to document the run-experiment command with usage examples, so that new users can understand how to run experiments.

#### Acceptance Criteria

1. THE README.md SHALL contain a `run-experiment` section placed after the existing single-trial command sections (after "Destroy Infrastructure") that includes: a prose description of the command's purpose (orchestrating multi-trial experiments), a parameters table listing every parameter with columns for Option, Required, and Description, and a step-by-step description of the trial execution flow.
2. THE parameters table SHALL document all required parameters (`--run-identifier`, `--autoscalers`, `--trials-per-autoscaler`, `--run-duration`, `--working-directory`, `--s3-bucket`) and all optional parameters with their default values, following the same table format used by existing command sections in the README.
3. THE README.md SHALL include at least one complete usage example as a fenced code block showing a valid `kasbench run-experiment` invocation that includes all required parameters and at least three optional parameters (`--trial-prefix`, `--var-file`, and `--auto-approve`).
4. IF the `--rerun-from-failed` or `--halt-on-error` parameter is documented, THEN THE description SHALL state the parameter's effect on trial execution flow (skipping completed steps or stopping at first error, respectively).
