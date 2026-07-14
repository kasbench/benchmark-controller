# Requirement 2: Additional Flows

## Modified Flow
### 1. Modify the existing `build-infrastructure` flow:
- Add an aditional option `--aws-region` for the AWS region in which the test will be executed.  The default is us-east-1.
- Add an additional required option `--s3-bucket`, which is the name of an S3 bucket in the specified AWS region (via --aws-region).
- Add an additiona required option `--run-duration`, which is the number of minutes for which the benchmark should run
- Save all of the above values so that they are available in the steps below 
- After the infrastructure build is complete, copy the following files to S3 in the specified region:

| Name on local host | S3 name |
| -- | -- |
| {working-directory}/{run-identifier}/{trial-identifier}/output/tofu_outputs.json | {s3-bucket}/{run-identifier/{trial-identifier}/infrastructure/tofu_outputs.json |
| {working-directory}/{run-identifier}/{trial-identifier}/benchmark-infrastructure/artifacts/{trial-identifier}/environment-description.json | {s3-bucket}/{run-identifier/{trial-identifier}/infrastructure/environment-description.json |
| {working-directory}/{run-identifier}/{trial-identifier}/benchmark-infrastructure/artifacts/{trial-identifier}/environment-description.md | {s3-bucket}/{run-identifier/{trial-identifier}/infrastructure/environment-description.md | 

- Enhance the `parse_tofu_outputs` function in `output_parser.py` to include the following additional fields:
    - control_plane_private_ip (output["control_plane"]["value"]["private_ip"])
    - amd_worker_private_ips (list of private_ip from output["worker_nodes"]["value"]["amd64"])
    - arm_worker_private_ips (list of private_ip from output["worker_nodes"]["value"]["arm64"])
    - globeco_dns (output["nlb"]["value"]["dns_name"])
    - globeco_port (output["nlb"]["value"]["listeners"]["http"]["port"]) <-- store as an int

Note: the `parse_tofu_outputs` function in `output_parser.py` already parses the Benchmark Runner host IP (benchmark_runner_public_ip) and SSH key-pair name (ssh_key_pair_name)

## New Flows
### 1. The `initialize-runner` flow

Add a new flow `initialize-runner` to the CLI that performs the following steps on the Benchmark Runner host:

- This flow is dependent upon the `build-infrastructure` flow.  If `build-infrastructure` has not been run, this flow should exit with an appropriate return code and descriptive error message.
- Pulls the KASBench runner from Docker Hub by executing the following command (or an equivalent) on the Benchmark Runner host (benchmark_runner_public_ip): `sudo docker pull kasbench/kasbench-runner:0.2.0`.  The IP of the Benchmark Runner is obtained in `output_parser.py`, as is the key-pair name.  The kasbench-runner version number should be configurable.
- Creates a docker network on the Benchmark Runner host named `kasbench`, using the following command or an equivalent: `sudo docker network create kasbench`.  If the network already exists, fail silently.
- Run the container on the Benchmark Runner host, using the following command (or an equivalent):
```bash
sudo docker run -d \
  --name kasbench-runner \
  --network kasbench \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /home/ubuntu/.ssh:/root/.ssh:ro \
  kasbench/kasbench-runner:0.2.0
```
- Iteratively execute the following command (or an equivalent) on the Benchmark Runner host until a status code 200 response is received: `curl -s http://{benchmark_runner_public_ip}:8080/status`.  The expected response will look like this: `{
  "status": "not-initialized",
  "startTime": null,
  "endTime": null,
  "loadGenerators": []
}`.  Iterate until the Benchmark Runner is available, up to a configurable timeout (initially set at 30 seconds). Wait 1 second between invocations.

- Initialize the Benchmark Runner using the following command as a guide.

```json
curl -s -X POST http://{benchmark_runner_public_ip}:8080/initialize \
-H "Content-Type: application/json" \
-d "{
  \"autoscaler\": \"{autoscaler}\",
  \"controlPlaneNode\": \"{control_plane_private_ip}\",
  \"amdWorkerNodes\": [\"{amd_worker_private_ips}\"],
  \"armWorkerNodes\": [\"{arm_worker_private_ips}\"],
  \"s3Bucket\": \"{s3-bucket}$",
  \"globecoUrl\": \"http://{globeco_dns}\",
  \"globecoPort\": {globeco_port} ,
  \"runIdentifier\": \"{run_identifier}\",
  \"trialIdentifier\": \"{trial_identifier\",
  \"runDurationMinutes\": {run_duration},
  \"skipKubernetesInstall\": false,
  \"skipManifestInstall\": false,
  \"forceManifestInstall\": true
}" 
```
Note that all values braces are variable substitutions.  The variables come from CLI command line options from the `build-infrastructure` flow or from the Open Tofu output.  If the above command returns a non-successful return code, exit with an appropriate return code and detailed error message.

- Iterate on the following command or an equivalent until it returns a 200 status code.  Iterate for up to 10 minutes (configurable): 
```bash
curl -s -X POST http://{benchmark_runner_public_ip}:8080/rollout/wait \
-H "Content-Type: application/json" \
-d '{"deploymentName": "globeco-confirmation-service", "namespace": "globeco", "timeout": 300}'
```

- Take a snapshot using the following command or an equivalent.  If it is unsuccesful, return with an appropriate error code and message:
```bash
curl -s -X POST http://{benchmark_runner_public_ip}:8080/snapshot \
-H "Content-Type: application/json" \
-d '{"phase": "pre"}' | jq .
```

- Update the benchmark database as appropriate for each step

### 2. The `benchmark-start` flow

Add a new flow `benchmark-start` to start the benchmark
- Execute the following command or an equivalent, returning an appropriate return code and message:
```bash
curl -s -X POST http://{benchmark_runner_public_ip}:8080/start \
-H "Content-Type: application/json" \
-d "{}" | jq .
```
- Update the benchmark database as appropriate

### 3. The `benchmark-monitor` flow
The `benchmark-monitor` flow monitors the progress of the benchmark and returns only when the benchmark is complete or a timeout has been reached.  It takes the following options:

| Option | Description |
| -- | -- |
| --timeout | Maximum run time in minutes. |
| --interval | Interval in seconds at which status is checked |
| --verbose | If supplied, prints a message at each `interval` |

When invoked and at each interval thereafter (up to timeout minutes), 

- Execute `curl -s http://{benchmark_runner_public_ip}:8080/status` (or equivalent)
  - If the result is a return code other than 200, return an appropriate return code and error message.  
  - Get the "status" field from the response object.  
  - If the status is running, print an appropriate message to the console (if verbose) and sleep until the next interval
  - If the status is "success" or "failed", update the benchmark database as appropriate and return with 0 return code and message.  Failed is a normal status.  It just means that some requests failed in the benchmark 
- This flow will be enhanced in the future with additional capabilities


### 4. The `benchmark-postprocessing` flow

Add a new flow `benchmark-postprocessing` that performs the following steps.  Note: for each step, check for a 200 status code.  If a step fails, exit the program with an appropriate return code and message.  Log each step to the benchmark database, as appropriate.

- Take a post-benchmark snapshot
```bash
curl -s  -X POST  "http://{benchmark_runner_public_ip}:8080/shutdown" 
```

- Export the metrics to S3:
```bash
curl -s  -X POST  "http://{benchmark_runner_public_ip}:8080/metrics/export" 
```

- Export the metadata to S3:
```bash
curl -s  -X POST  "http://{benchmark_runner_public_ip}:8080/metrics/export" 
```

- Export the Prometheus time series database to S3:
```bash
curl -s  -X POST  "http://{benchmark_runner_public_ip}:8080/metrics/export" 
```

- Export output to S3:
```bash
curl -s  -X POST  "http://{benchmark_runner_public_ip}:8080/output/export" 
```

- Export databases to S3:
```bash
curl -s  -X POST  "http://{benchmark_runner_public_ip}:8080/db/export" 
```



### 4. The `destroy-infrastructure` flow

- This process is invoked from the command line with the `destroy-infrastructure` argument.  The following command line options are available:
    - `--working-dir`: This must be a directory previously created through the init process (mandatory).    
    - `--run-identifier`: A text string used to identify this run of the benchmark. Must have been previously initialized (mandatory).
    - `--trial-identifier`: A text string used to identify this trial (mandatory).
    - `--auto-approve`: If supplied, automatically approve the apply without prompting the user to approve.
    - `--var-file`: a tfvars file to be used with the call to `tofu destroy`.  This argument may appear more than once. Optional. 
    -  `--var`: a variable name-value pair (e.g., "environment=production").  This argument may appear more than one. Optional.
    - `--no-apply`: If this argument is supplied, the `tofu destroy` step will be skipped.

- Execute the following command.  If the command does not return a 200 status code, exit with an appropriate return code and message:
```bash
curl -s  -X POST  "http://localhost:8080/shutdown" 
```
- Wait 5 minutes (configurable) to allow time for the EBS volumes to free up.  If we destroy the infrastructure before the EBS volumes are freed up, they will not be deleted and will continue to accrue cost.  Print a message to the console every 30 seconds so it's obvious that the program is waiting.
- Navigate to the Open Tofu working directory following the logic from `build-infrastructure`
- Execute the `tofu destroy` command following the conventions established in `build-infrastructure`
- Update the benchmark database as appropriate

## Other

### 1. Update README.md to reflect the changes made in this enhancement.