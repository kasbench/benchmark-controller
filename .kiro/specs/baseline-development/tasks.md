# Implementation Plan: Baseline Development

## Overview

Implement the KASBench Controller CLI application with `init` and `build-infrastructure` subcommands. The implementation uses a src-layout Python package with `click` for CLI, `structlog` for JSON Lines logging, `sqlite3` for persistence, `subprocess` for tofu integration, and `httpx` for repository downloads. Tasks are ordered to build foundational modules first, then compose them into command flows.

## Tasks

- [x] 1. Project scaffolding and core modules
  - [x] 1.1 Configure pyproject.toml with dependencies and src-layout
    - Update `pyproject.toml` to add dependencies: click, structlog, httpx
    - Add dev dependencies: pytest, hypothesis, pytest-mock, respx
    - Configure package as src-layout with `kasbench_controller` package
    - Add console script entry point `kasbench = "kasbench_controller.cli:cli"`
    - Remove the existing `main.py` (replaced by package entry point)
    - _Requirements: 1.1_

  - [x] 1.2 Create package structure and exceptions module
    - Create `src/kasbench_controller/__init__.py`
    - Create `src/kasbench_controller/commands/__init__.py`
    - Create `src/kasbench_controller/exceptions.py` with: `KasbenchError`, `DatabaseError`, `TofuError`, `RepositoryDownloadError`, `ValidationError`, `DuplicateTrialError`
    - `TofuError` stores message, stdout, stderr, return_code
    - `RepositoryDownloadError` stores message, url, status_code, elapsed
    - _Requirements: 1.6_

  - [x] 1.3 Create models module
    - Create `src/kasbench_controller/models.py`
    - Implement `RunContext` dataclass with working_directory, run_identifier, computed run_directory and db_path
    - Implement `TrialContext` dataclass with run_context, trial_identifier, autoscaler, computed trial_directory, tofu_directory, output_directory
    - Implement `TofuOutputs` dataclass with benchmark_runner_public_ip, ssh_key_pair_name, raw_json
    - _Requirements: 2.3, 4.7_

  - [x] 1.4 Create structured logging module
    - Create `src/kasbench_controller/logging.py`
    - Implement `configure_logging(log_file, dry_run)` that configures structlog for JSON Lines output to stdout
    - When log_file is provided, add a file handler that writes JSON Lines to the specified path
    - Implement `log_step(logger, step, outcome, **kwargs)` that emits a structured entry with ISO 8601 UTC timestamp, step, and outcome
    - Implement `log_dry_run(logger, operation, details)` for dry-run reporting
    - If log file cannot be created, write error to stderr and exit with code 1
    - _Requirements: 1.2, 1.3, 1.4, 1.7_

- [x] 2. Database module
  - [x] 2.1 Implement database schema creation and verification
    - Create `src/kasbench_controller/database.py`
    - Implement `DatabaseManager.__init__(db_path)` that opens connection with foreign keys enabled
    - Implement `create_schema()` that creates `trials` and `events` tables per design SQL
    - The `trials.status` column must have a CHECK constraint for valid values
    - The `events.trial_id` must have a FOREIGN KEY to `trials.trial_id`
    - Implement `verify_schema()` that confirms both tables exist
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7_

  - [x] 2.2 Implement database operations for trial management
    - Implement `insert_trial(run_identifier, trial_identifier, autoscaler)` returning generated trial_id
    - Implement `check_duplicate_trial(run_identifier, trial_identifier)` returning bool
    - Implement `update_trial_after_apply(trial_id, public_ip, key_pair_name)` setting status=INIT, benchmark_runner_public_ip, ssh_key_pair_name, last_update_time; infra_end_time stays NULL
    - Implement `record_infra_start_time(trial_id)` setting infra_start_time to current timestamp
    - Raise `DatabaseError` with path and underlying error on failures
    - Raise `DuplicateTrialError` when duplicate detected
    - _Requirements: 3.6, 6.1, 6.2, 6.3, 6.4, 8.9, 9.6_

  - [x]* 2.3 Write property tests for database module
    - **Property 3: Status column constraint enforcement**
    - **Property 5: Trial record insertion preserves provided values**
    - **Property 6: Duplicate trial detection**
    - **Property 9: infra_end_time remains NULL after build-infrastructure**
    - **Validates: Requirements 3.4, 6.1, 6.3, 8.10, 9.6**

- [x] 3. CLI framework setup
  - [x] 3.1 Implement CLI entry point with click group
    - Create `src/kasbench_controller/cli.py`
    - Define `@click.group()` with `--log` and `--dry-run` global options
    - Store options in `ctx.obj` dict for subcommand access
    - Configure logging in the group callback
    - Wire up subcommands (init, build-infrastructure)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [x]* 3.2 Write property test for structured logging
    - **Property 1: Structured log entries contain required fields**
    - **Validates: Requirements 1.2**

- [x] 4. Init command implementation
  - [x] 4.1 Implement init subcommand
    - Create `src/kasbench_controller/commands/init.py`
    - Implement `init_cmd` with --working-directory, --run-identifier, --force options
    - Create working directory with `mkdir(parents=True, exist_ok=True)`
    - If run directory exists and --force not set, exit with error
    - If run directory exists and --force set, delete and recreate
    - Create run directory
    - Create benchmark.db with schema via DatabaseManager
    - Verify database after creation
    - Handle dry-run mode: report planned operations without executing
    - Catch `KasbenchError` and exit with code 1 on failure
    - Exit with code 0 on success
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.6, 3.7_

  - [x]* 4.2 Write property test for path construction
    - **Property 2: Path construction is deterministic joining**
    - **Validates: Requirements 2.3, 4.7**

  - [x]* 4.3 Write unit tests for init command
    - Test successful initialization creates directory and database
    - Test --force flag removes existing directory
    - Test error when directory exists without --force
    - Test dry-run mode reports operations without side effects
    - Test filesystem error handling
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Repository download module
  - [x] 6.1 Implement repository downloader
    - Create `src/kasbench_controller/repository.py`
    - Implement `RepositoryDownloader` with target_dir and dry_run params
    - Implement `download_and_extract()` using httpx to download GitHub zipball URL
    - Set 120-second timeout on the HTTP request
    - Extract zip contents, stripping the top-level directory prefix so files land directly in target_dir
    - Implement `_cleanup_unwanted_files()` to remove .kiro, requirements, .gitignore, .git (skip missing items silently)
    - Implement retry logic: up to 3 attempts with 5-second delay for transient errors (connection errors, 5xx)
    - Raise `RepositoryDownloadError` with URL, status code, and elapsed time on failure
    - Support dry-run mode
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x]* 6.2 Write property test for zip extraction
    - **Property 4: Zip extraction strips top-level directory prefix**
    - **Validates: Requirements 5.2**

  - [x]* 6.3 Write unit tests for repository module
    - Test successful download and extraction with mocked httpx (using respx)
    - Test retry logic on transient errors
    - Test immediate failure on 404
    - Test timeout handling
    - Test cleanup of unwanted files
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

- [x] 7. Tofu runner module
  - [x] 7.1 Implement tofu subprocess wrapper
    - Create `src/kasbench_controller/tofu.py`
    - Implement `TofuRunner` with working_dir and dry_run params
    - Implement `init()` that runs `tofu init` via subprocess.run in working_dir
    - Implement `apply(var_files, variables, run_id, auto_approve)` that builds and runs `tofu apply`
    - Implement `plan(var_files, variables, run_id)` that runs `tofu plan`
    - Implement `output_json()` that runs `tofu output -json` and parses the JSON result
    - Implement `_resolve_var_file(var_file)` - if no path separator, resolve relative to environments/ subdirectory; otherwise use as-is
    - Implement `_build_var_args(var_files, variables, run_id)` - order: var-files first (in order), then vars (in order), then run_id last
    - Return `TofuResult` dataclass with return_code, stdout, stderr, success
    - Raise `TofuError` on non-zero exit codes
    - Support dry-run mode
    - _Requirements: 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.8_

  - [x]* 7.2 Write property tests for tofu module
    - **Property 7: Tofu command argument ordering**
    - **Property 8: Var-file path resolution**
    - **Validates: Requirements 8.2, 8.3**

  - [x]* 7.3 Write unit tests for tofu module
    - Test init command construction and execution (mocked subprocess)
    - Test apply with auto-approve flag
    - Test var-file resolution (filename only vs full path)
    - Test argument ordering with multiple var-files and variables
    - Test error handling on non-zero exit
    - Test output_json parsing
    - _Requirements: 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.8_

- [x] 8. Build-infrastructure command implementation
  - [x] 8.1 Implement build-infrastructure subcommand
    - Create `src/kasbench_controller/commands/build_infrastructure.py`
    - Implement `build_infrastructure_cmd` with all required click options
    - Validate run directory exists and contains valid benchmark.db
    - Handle trial directory: check existence, apply --force logic
    - Create trial directory and output subdirectory
    - Download repository via RepositoryDownloader
    - Check for duplicate trial, then insert trial record via DatabaseManager
    - Run `tofu init` via TofuRunner
    - If --no-apply, log early termination and exit 0
    - If not --auto-approve, run plan and prompt user for approval
    - Record infra_start_time, then run `tofu apply`
    - Capture outputs via `tofu output -json`
    - Parse benchmark_runner.public_ip and ssh_key_pair_name from JSON output
    - Write outputs to `output/tofu_outputs.json`
    - Update trial record with status=INIT, public_ip, key_pair_name
    - Handle dry-run mode for all operations
    - Catch `KasbenchError` and exit with code 1 on failure
    - Exit with code 0 on success
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 5.1, 6.1, 6.2, 6.3, 6.4, 7.1, 7.4, 8.1, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_

  - [x] 8.2 Implement output parsing logic
    - Implement parsing of `tofu output -json` structure
    - Extract `output["benchmark_runner"]["value"]["public_ip"]`
    - Extract `output["ssh_key_pair_name"]["value"]`
    - Raise `ValidationError` with missing key names if keys not found
    - Handle `<sensitive>` markers as None values without error
    - _Requirements: 9.3, 9.4, 9.5, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x]* 8.3 Write property tests for output parsing
    - **Property 10: JSON output value extraction round-trip**
    - **Property 11: Missing key error identification**
    - **Property 12: Sensitive marker handling**
    - **Validates: Requirements 9.3, 9.4, 9.5, 10.1, 10.4, 10.6**

- [x] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Integration tests
  - [x]* 10.1 Write integration tests for init flow
    - Test full init flow end-to-end with real filesystem and SQLite
    - Test init with --force replacing existing run
    - Test init with --dry-run producing log output without side effects
    - Test init failure scenarios (permission errors, invalid paths)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.7_

  - [x]* 10.2 Write integration tests for build-infrastructure flow
    - Test full build-infrastructure flow with mocked subprocess and httpx
    - Test --no-apply early exit after tofu init
    - Test --force replacing existing trial directory
    - Test --dry-run reporting all planned operations
    - Test validation failures (missing run directory, missing database)
    - Test duplicate trial detection
    - Test user declining plan approval
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 7.4, 8.6, 8.7, 9.8_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The `infra_end_time` column intentionally stays NULL after build-infrastructure (set by future destruction flow)
- Uses `tofu output -json` for output parsing (not HCL parsing)
- All subprocess calls use `subprocess.run()` for transparency and stdout/stderr capture

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["2.1", "3.1"] },
    { "id": 3, "tasks": ["2.2", "3.2"] },
    { "id": 4, "tasks": ["2.3", "4.1", "6.1", "7.1"] },
    { "id": 5, "tasks": ["4.2", "4.3", "6.2", "6.3", "7.2", "7.3"] },
    { "id": 6, "tasks": ["8.1"] },
    { "id": 7, "tasks": ["8.2"] },
    { "id": 8, "tasks": ["8.3"] },
    { "id": 9, "tasks": ["10.1", "10.2"] }
  ]
}
```
