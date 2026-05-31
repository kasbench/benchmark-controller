# Requirements Document

## Introduction

The KASBench Controller is a Python CLI application that orchestrates Kubernetes autoscaling benchmark execution. It runs on a Bastion Host and manages benchmark trials via AWS infrastructure provisioned with Open Tofu. This baseline development covers two core flows: `init` (initializing a new experimental run) and `build-infrastructure` (provisioning AWS infrastructure for a benchmark trial).

## Glossary

- **Controller**: The KASBench Controller CLI application, the system under development.
- **Bastion_Host**: The pre-existing EC2 instance in VPC 1 where the Controller executes.
- **Working_Directory**: The top-level directory where all data created by the Controller is maintained.
- **Run_Directory**: A subdirectory of the Working_Directory named by the run identifier, containing the SQLite database and trial directories.
- **Trial_Directory**: A subdirectory of the Run_Directory named by the trial identifier, containing benchmark infrastructure and output files.
- **Open_Tofu_Directory**: The `benchmark-infrastructure` subdirectory within a Trial_Directory containing Open Tofu HCL files and state.
- **Benchmark_Database**: The SQLite database file (`benchmark.db`) located in the Run_Directory that stores trial and event records.
- **Benchmark_Runner**: The EC2 instance in the public subnet of the benchmark VPC that executes benchmark workloads.
- **Open_Tofu**: The infrastructure-as-code tool (fork of Terraform) used to provision AWS resources.
- **Trial**: A single benchmark execution against a specific autoscaler configuration, including infrastructure provisioning, benchmark execution, and cleanup.
- **Run**: A collection of trials that together form a complete benchmark experiment.
- **HCL_Output**: The output produced by `tofu output` after a successful apply, formatted in HCL syntax.

## Requirements

### Requirement 1: CLI Framework and Structured Logging

**User Story:** As a researcher, I want a CLI application with structured logging, so that I can audit every step of benchmark execution with timestamps.

#### Acceptance Criteria

1. THE Controller SHALL provide a command-line interface with subcommands `init` and `build-infrastructure`.
2. THE Controller SHALL emit structured log entries as JSON Lines to standard output for every operational step, where each entry includes at minimum: an ISO 8601 UTC timestamp, a step description, and an outcome field indicating success or failure.
3. WHEN the `--log` option is provided with a filename, THE Controller SHALL write structured log entries to the specified file in addition to standard output.
4. WHEN the `--dry-run` option is provided, THE Controller SHALL report each operation that would be performed without executing the operation.
5. WHEN the `--dry-run` option is provided, THE Controller SHALL exit with return code 0 after reporting all planned operations.
6. IF a subcommand encounters an unrecoverable error, THEN THE Controller SHALL exit with return code 1 and emit a structured log entry containing the error description and the operation context in which the failure occurred.
7. IF the `--log` option is provided and the specified file cannot be created or written to, THEN THE Controller SHALL emit an error message to standard error indicating the file path and failure reason, and SHALL exit with return code 1.

### Requirement 2: Init Flow - Run Directory Creation

**User Story:** As a researcher, I want to initialize a new experimental run, so that I have a clean workspace with a database ready for recording trial data.

#### Acceptance Criteria

1. WHEN the `init` subcommand is invoked, THE Controller SHALL require the `--working-directory` and `--run-identifier` arguments.
2. WHEN the Working_Directory does not exist, THE Controller SHALL create the Working_Directory including any intermediate parent directories.
3. THE Controller SHALL create the Run_Directory by joining the Working_Directory path with the run identifier.
4. WHEN the Run_Directory already exists and the `--force` flag is not provided, THE Controller SHALL exit with a non-zero return code and an error message stating the directory already exists.
5. WHEN the Run_Directory already exists and the `--force` flag is provided, THE Controller SHALL delete the existing Run_Directory and all its contents before recreating it.
6. IF the Run_Directory cannot be created due to a filesystem error, THEN THE Controller SHALL exit with a non-zero return code and a detailed error message including the filesystem error.
7. WHEN the `init` subcommand completes without error, THE Controller SHALL exit with return code 0.

### Requirement 3: Init Flow - Database Creation

**User Story:** As a researcher, I want a SQLite database created during initialization, so that trial metadata and events are persisted for analysis.

#### Acceptance Criteria

1. WHEN the Run_Directory is successfully created, THE Controller SHALL create a SQLite database file named `benchmark.db` in the Run_Directory.
2. THE Controller SHALL create a `trials` table in the Benchmark_Database with the following columns: `trial_id` (integer, primary key, autoincrement), `status` (text, not null, default "PENDING"), `run_identifier` (text, nullable), `trial_identifier` (text, nullable), `autoscaler` (text, not null), `record_created_time` (datetime, not null, default current timestamp), `benchmark_runner_public_ip` (text, nullable), `ssh_key_pair_name` (text, nullable), `last_update_time` (datetime, not null, default current timestamp), `infra_start_time` (datetime, nullable), `infra_end_time` (datetime, nullable), `cleanup_start_time` (datetime, nullable), `cleanup_end_time` (datetime, nullable), `benchmark_start_time` (datetime, nullable), `benchmark_end_time` (datetime, nullable), `unresponsive_checks` (integer, not null, default 0).
3. THE Controller SHALL create an `events` table in the Benchmark_Database with the following columns: `event_id` (integer, primary key, autoincrement), `trial_id` (integer, not null, foreign key referencing trials.trial_id), `event_time` (datetime, not null, default current timestamp), `event_type` (text, not null), `event_request` (text, nullable), `event_message` (text, nullable).
4. THE Controller SHALL enforce the `status` column to accept only the values: PENDING, INIT, RUNNING, CLEANUP, SUCCESS, FAIL, TERMINATED, UNKNOWN.
5. THE Controller SHALL enable foreign key enforcement on the Benchmark_Database connection so that any INSERT or UPDATE on `events.trial_id` referencing a non-existent `trials.trial_id` is rejected.
6. IF the database creation fails, THEN THE Controller SHALL exit with a non-zero return code and an error message that includes the file path attempted and the underlying database error description.
7. WHEN the Benchmark_Database is successfully created, THE Controller SHALL verify the database is accessible by opening a connection and confirming both the `trials` and `events` tables exist before returning success.

### Requirement 4: Build-Infrastructure Flow - Validation and Setup

**User Story:** As a researcher, I want the build-infrastructure command to validate prerequisites before provisioning, so that I receive clear errors if the environment is not properly initialized.

#### Acceptance Criteria

1. WHEN the `build-infrastructure` subcommand is invoked without any of the required arguments (`--working-directory`, `--run-identifier`, `--trial-identifier`, `--autoscaler`), THE Controller SHALL exit with a non-zero return code and an error message indicating which required arguments are missing.
2. WHEN the `build-infrastructure` subcommand is invoked, THE Controller SHALL validate that the Run_Directory (formed by joining `--working-directory` and `--run-identifier`) exists and contains a `benchmark.db` file that is a readable SQLite database containing the `trials` and `events` tables.
3. IF the Run_Directory does not exist or does not contain a `benchmark.db` file that is a readable SQLite database with the `trials` and `events` tables, THEN THE Controller SHALL exit with a non-zero return code and an error message indicating the run has not been initialized.
4. IF the Trial_Directory already exists and the `--force` flag is not provided, THEN THE Controller SHALL exit with a non-zero return code and an error message stating the trial directory already exists.
5. IF the Trial_Directory already exists and the `--force` flag is provided, THEN THE Controller SHALL delete the existing Trial_Directory and all its contents before recreating it.
6. IF the Trial_Directory cannot be created or an existing Trial_Directory cannot be deleted when `--force` is provided, THEN THE Controller SHALL exit with a non-zero return code and an error message indicating the file system operation that failed.
7. THE Controller SHALL create the Trial_Directory by joining the Run_Directory path with the trial identifier.
8. THE Controller SHALL create an `output` subdirectory within the Trial_Directory.

### Requirement 5: Build-Infrastructure Flow - Repository Download

**User Story:** As a researcher, I want the benchmark infrastructure code downloaded automatically, so that each trial uses the latest infrastructure definitions.

#### Acceptance Criteria

1. THE Controller SHALL download the contents of the `main` branch of the `https://github.com/kasbench/benchmark-infrastructure` repository into the `benchmark-infrastructure` subdirectory of the Trial_Directory within 120 seconds.
2. THE Controller SHALL place the repository contents directly in the `benchmark-infrastructure` subdirectory without an intermediate directory level.
3. WHEN the download completes, THE Controller SHALL delete the `.kiro` subdirectory, the `requirements` subdirectory, the `.gitignore` file, and the `.git` directory from the downloaded `benchmark-infrastructure` directory, skipping any items that do not exist without treating their absence as an error.
4. IF the repository download fails or the 120-second timeout is exceeded, THEN THE Controller SHALL exit with a non-zero return code and an error message including the URL attempted, the HTTP status code or network error description, and the elapsed time of the attempt.
5. IF the repository download fails due to a transient network error, THEN THE Controller SHALL retry the download up to 3 times with a 5-second delay between attempts before exiting with a non-zero return code.

### Requirement 6: Build-Infrastructure Flow - Database Record Insertion

**User Story:** As a researcher, I want each trial recorded in the database at creation time, so that I can track trial status from the moment provisioning begins.

#### Acceptance Criteria

1. WHEN the Trial_Directory is successfully created and the repository is downloaded, THE Controller SHALL insert a record into the `trials` table with: `status` set to "PENDING", `run_identifier` set to the provided run identifier, `trial_identifier` set to the provided trial identifier, `autoscaler` set to the provided autoscaler value, `record_created_time` set to the current timestamp, `last_update_time` set to the current timestamp, and all other nullable columns set to NULL or their schema-defined defaults.
2. WHEN the database insert completes successfully, THE Controller SHALL use the generated `trial_id` as the identifier for all subsequent database updates within the same build-infrastructure invocation.
3. IF a record with the same `run_identifier` and `trial_identifier` combination already exists in the `trials` table, THEN THE Controller SHALL exit with a non-zero return code and an error message indicating the duplicate trial.
4. IF the database insert fails, THEN THE Controller SHALL leave the already-created Trial_Directory and downloaded repository intact, exit with a non-zero return code, and log an error message that includes the database error reason.

### Requirement 7: Build-Infrastructure Flow - Open Tofu Init

**User Story:** As a researcher, I want Open Tofu initialized automatically, so that the infrastructure provisioning workspace is ready for apply.

#### Acceptance Criteria

1. WHEN the database record is inserted, THE Controller SHALL execute `tofu init` in the Open_Tofu_Directory.
2. THE Controller SHALL log the standard output and standard error from the `tofu init` command.
3. IF `tofu init` exits with a non-zero return code, THEN THE Controller SHALL exit with a non-zero return code and a detailed error message including the tofu init output.
4. WHEN the `--no-apply` flag is provided and `tofu init` succeeds, THE Controller SHALL log a message indicating early termination due to the `--no-apply` flag and exit with return code 0.

### Requirement 8: Build-Infrastructure Flow - Open Tofu Apply

**User Story:** As a researcher, I want infrastructure provisioned via Open Tofu apply with configurable variables, so that I can customize the benchmark environment per trial.

#### Acceptance Criteria

1. WHEN `tofu init` succeeds and the `--no-apply` flag is not provided, THE Controller SHALL execute `tofu apply` in the Open_Tofu_Directory.
2. THE Controller SHALL pass `--var-file` arguments to `tofu apply` in the order specified on the command line, followed by `--var` arguments in the order specified on the command line, followed by the variable `run_id` set to the trial identifier.
3. WHEN a `--var-file` argument contains only a filename without a path separator, THE Controller SHALL resolve the file relative to the `environments` subdirectory of the Open_Tofu_Directory.
4. IF a resolved `--var-file` path does not exist on the filesystem, THEN THE Controller SHALL exit with a non-zero return code and a message indicating which file could not be found and the resolved path that was checked.
5. IF the `--auto-approve` flag is provided, THEN THE Controller SHALL pass the `-auto-approve` flag to `tofu apply`.
6. IF the `--auto-approve` flag is not provided, THEN THE Controller SHALL execute `tofu plan`, display the plan output to the user, and prompt for yes/no approval before executing `tofu apply`.
7. IF the user does not approve the plan, THEN THE Controller SHALL exit with a non-zero return code and a message indicating the user declined the apply.
8. IF `tofu apply` exits with a non-zero return code, THEN THE Controller SHALL exit with a non-zero return code and an error message that includes the stderr and stdout output from the `tofu apply` process.
9. THE Controller SHALL record `infra_start_time` in the trial database record with the timestamp captured immediately before executing `tofu apply`.
10. WHEN `tofu apply` completes successfully, THE Controller SHALL capture the Open Tofu outputs as JSON, write them to a file named `kasbench_infra_outputs.json` in the `output` subdirectory of the trial directory, and update the trial database record by setting `status` to `INIT`, `benchmark_runner_public_ip` to the value at `benchmark_runner.public_ip` in the outputs, `ssh_key_pair_name` to the value at `ssh_key_pair_name` in the outputs, and `last_update_time` to the current timestamp. The `infra_end_time` column SHALL remain NULL until the infrastructure destruction flow sets it.

### Requirement 9: Build-Infrastructure Flow - Output Capture and Database Update

**User Story:** As a researcher, I want infrastructure outputs captured and recorded, so that subsequent steps can connect to provisioned resources.

#### Acceptance Criteria

1. WHEN `tofu apply` succeeds, THE Controller SHALL capture the Open Tofu outputs by executing `tofu output -json` in the Open_Tofu_Directory.
2. WHEN outputs are captured, THE Controller SHALL write the captured JSON outputs to a file named `tofu_outputs.json` in the `output` subdirectory of the Trial_Directory.
3. WHEN outputs are captured, THE Controller SHALL parse the `benchmark_runner.public_ip` value from the JSON output.
4. WHEN outputs are captured, THE Controller SHALL parse the `ssh_key_pair_name` value from the JSON output.
5. IF the JSON output does not contain the `benchmark_runner.public_ip` key or the `ssh_key_pair_name` key, THEN THE Controller SHALL exit with a non-zero return code and an error message identifying which key is missing.
6. WHEN outputs are parsed successfully, THE Controller SHALL update the trial database record with: `status` set to "INIT", `benchmark_runner_public_ip` set to the parsed public IP value, `ssh_key_pair_name` set to the parsed key pair name, and `last_update_time` set to the current timestamp. The `infra_end_time` column SHALL remain NULL until the infrastructure destruction flow sets it.
7. IF the `tofu output` command, file write, or database update fails, THEN THE Controller SHALL exit with a non-zero return code and an error message that includes the failed operation name and the underlying error returned by the operation.
8. WHEN all steps complete without error, THE Controller SHALL exit with return code 0.

### Requirement 10: Open Tofu Output Parsing

**User Story:** As a researcher, I want the Controller to correctly parse Open Tofu HCL-formatted output, so that infrastructure details are reliably extracted regardless of output format.

#### Acceptance Criteria

1. THE Controller SHALL parse Open Tofu output in HCL format (the default format produced by `tofu output`), supporting the following value types: quoted strings, integers, booleans, maps, lists, and nested combinations of these types up to 4 levels of nesting depth.
2. THE Controller SHALL support extracting nested values from HCL map structures using dot-notation paths (e.g., `benchmark_runner.public_ip`) and bracket-index notation for list access (e.g., `worker_nodes.amd64[0].private_ip`).
3. WHEN the Controller parses valid HCL output produced by `tofu output` and extracts a value by key path, THE Controller SHALL return the value as a string matching the literal text between the quotes in the HCL output for string values, or the literal numeric text for numeric values.
4. IF the expected output keys are missing from the Open Tofu output, THEN THE Controller SHALL exit with a non-zero return code and an error message listing each missing key by name.
5. IF the Open Tofu output is empty or cannot be parsed as valid HCL, THEN THE Controller SHALL exit with a non-zero return code and an error message indicating that parsing failed.
6. WHEN the Controller encounters a `<sensitive>` marker as a value during HCL parsing, THE Controller SHALL represent that value as a null or empty indicator and SHALL NOT raise a parse error.

---

## Notes and Identified Issues

The following issues were identified in the source requirement document (`requirements/requirement_001.md`):

1. **Output format mismatch**: The example file `examples/kasbench_infra_outputs.json` is in HCL format, not JSON, despite the `.json` extension. The Controller must parse HCL output from `tofu output` (or use `tofu output -json` for JSON format).

2. **Database column name inconsistency**: The requirement document references `benchmark_runner_public_dns` in the database update mapping, but the `trials` table schema defines `benchmark_runner_public_ip`. This requirements document uses `benchmark_runner_public_ip` consistently, matching the schema.

3. **CLI argument naming inconsistency**: The `init` flow uses `--working-directory` while `build-infrastructure` uses `--working-dir`. This requirements document standardizes on `--working-directory` for both subcommands for consistency.

4. **TofuPy dependency**: The source requirement mentions TofuPy, but no such dependency exists in `pyproject.toml` and TofuPy does not appear to be a widely available Python package. This requirements document specifies calling `tofu` directly via subprocess, which is the reliable approach.

5. **infra_start_time mapping**: The source requirement maps `infra_start_time` to the post-apply database update. This requirements document places `infra_start_time` at the start of `tofu apply` execution. The `infra_end_time` is intentionally left NULL during `build-infrastructure` and will be set by the future infrastructure destruction flow.
