Requirement 3: Run Experiment

This requirement adds `run-experiment` as a new CLI command.  Whereas all the previous commands comprise the lifecycle of a single trial, this command runs a full experiment consisting of multiple trials.  

## Parameters

| Parameter | Required | Default Value | Definition |
| --- | --- | --- | --- |
| `--run-identifier` | Yes | N/A | A unique identifier for the experiment |
| `--trial-prefix` | No | "trial" | A prefix for each trial within the experiment.  The trial identifer will be the prefix concatenated with a sequentially assigned number in the form 0001, 0002, ...
| `--autoscalers` | Yes | N/A | Comma separated list of autoscalers.  Currently available autoscalers are hpa, vpa, keda, and none.  At least one of the four autoscaler options must be supplied.  Each option may appear more than once. |
| `--trials-per-autoscaler` | Yes | N/A | The number of trials conducted for each autoscaler.  The number of trials is the number of autoscalers passed with --autoscaler multiplied by --trials-per-autoscaler |
| `--run-duration` | Yes | N/A | Benchmark run duration in minutes |
| `--working-directory` | Yes | N/A | Top-level directory for all benchmark data |
| `--aws-region` | No | `us-east-1 | AWS region for infrastructure deployment|
| `--s3-bucket` | Yes | N/A | S3 bucket for artifact storage |
| `--var-file` | No | None | Tofu var-file (repeatable). Filenames without path separators resolve to `environments/` |
| `--var` | No | None | Tofu variable assignment as `key=value` (repeatable) |
| `--auto-approve` | No | No | Skip interactive plan approval |
| `--runner-version` | No | '0.2.6' | KASBench Runner Docker image version |
| `--health-timeout` | No | 30 | Health check polling timeout in seconds |
| `--rollout-timeout` | No | 600 | Rollout wait timeout in seconds |
| `--cluster-cidr-range` | No | `"10.244.0.0/16"` | Pod network CIDR to pass to the Runner.  In the future, this will be expanded into a list of CIDR ranges to allow more than one trial to be run simultaneously. |
| `--role-params` | No | None | Per-role load generation overrides as a JSON string. Each role value must include `baseLoadIntensity`, `baseDelayPercentage`, and `spawnRate`.  See --role-params in the benchmark-start CLI command. |
| `--random-seed` | No | None | If supplied, will be used to seed the random generator |
 `--auto-approve` | No | False | Skip interactive approval for tofu destroy |
| `--ebs-wait` | No | 300 | Seconds to wait for EBS volume detachment |
| `--rerun-from-failed` | No | False | If true, this is a rerun.  Skip the steps that have aleady run successfully and start with the first failed step |
| `--halt-on-error` | No | False | Stop processing immediately at the first error.  Used for testing and debugging.  Not applicable to actual benchmark runs |

## Flow

- Each trial must be assigned a unique sequential identifier by concatenating `--trial-prefix` with a sequential, zero padded four digit number (0001, 0002, $\dots$).  Trial identifiers must never be reused.
- Each autosaler must be tested exactly the number of times specified by `--trials-per-autoscaler`.
- Autoscalers will be selected at random.  If `--random-seed` is supplied, use it to seed the random number generator.  Each autoscaler must be selected no more than the number of times specified by `--trials-per-autoscaler`.  If an autoscaler is randomly selected more than the maximum number of times, the selection should be discarded and a new selection made.
- Each trial progresses through the following steps.  If any step ends in an error (non-zero return code), abort the current trial and proceed to the next trial.  See the instructions on aborting below.  Do not re-use the trial number.
    - Call the CLI's `kasbench init` (or equivalent) passing working-directory and run-identifier
    - Call `kasbench build-infrastructure` (or the equivalent), passing corresponding fields from the parameters above, including the randomly generated autoscaler.
    - Wait a configurable amount of time (initially 5 minutes).
    - Call `kasbench initialize-runner` (or the equivalent), passing corresponding fields from the parameters above.
    - Call `kasbench benchmark-start` (or the equivalent), passing corresponding fields from the parameters above.  Only pass role-params if supplied as an argument.
    - Call `kasbench benchmark-monitor` (or the equivalent) using a timeout at least five minutes greater than `--run-duration` (for safety).  The benchmark may end in success or fail.  Only abort on an error.
    - Call `kasbench benchmark-postprocessing` (or the equivalent), passing the corresponding fields from the parameters above.
    - Call `kasbench shutdown` (or the equivalent), passing the corresponding fields from the parameters above.
    - Call `kasbench destroy-infrastructure` (or the equivalent), passing the corresponding fields from the parameters above.
    - Upload detailed logs from the run to S3 with the prefix {s3-bucket}/{run-identifier}/{generated trial identifier}/benchmark-results.   Be sure to capture errors in as much detail as possible.  Verbose logs are preferred, since the main purpose will be for debugging.


## Aborting 

- if `--halt-on-error` is True, report the error and stop processing.  Otherwise, proceed to the next steps.
- Attempt to run `kasbench destroy-infrastructure`.  If successful, continue with the next iteration.  Otherwise, proceed to the next step.
- Attempt to run `tofu destroy -var-file={var file passed as parameters, repeate as necessary} -var={var passed as parameters, repeate as necessary} --auto-approve` from the `/{working-directory}/benchmarks/{run identifier}/trial identifier)/benchmark-infrastructure` directory.  If successful, continue with the next iteration.  Otherwise, abort processing with an appropriate error message.  Subsequent iterations will fail if the infrastructure has not been fully destroyed.


## Notes
- Output progress to std out and to a file.  
- The `--rerun-from-failed` = True will necessitate tracking and persisting completed steps to enable restarting at the first failed step. 
- Keep track of progress in a file in {s3-bucket}/{run-identifier}.  The file should have a record of the value of all parameters  
- When starting a run, look to see if the s3-bucket}/{run-identifier} exists.  If it does, this is a rerun.  Compare arguments to those stored in the file created in the previous step.  If they don't match, fail the job.  A rerun is not possible with different parameter values.  If they match, continue processing from where it left off.  If the experiment was already complete (all trials for all autoscalers), exit with an appropriate message.  
- You may add to the parameter list if anything is missing
- Update [README.md](../README.md) with a detailed description of this command and an example.
