# KASBench Benchmark Runner

A Python FastAPI microservice that orchestrates the full lifecycle of a KASBench benchmark trial. The Runner configures Kubernetes clusters via SSH, deploys the GlobeCo application suite, launches load generator containers, and manages benchmark execution from start to completion.

## Architecture

The Runner runs as a Docker container on the Benchmark Runner node within the `kasbench` Docker bridge network. It is invoked by the Benchmark Controller on the Bastion Host and follows a strict linear lifecycle:

```
initialize → start → monitor → collect results
```

### Lifecycle States

| State | Description |
|-------|-------------|
| `not-initialized` | Application started, awaiting initialization |
| `not-started` | Initialization complete, ready to start benchmark |
| `running` | Benchmark in progress, load generators active |
| `success` | All generators completed successfully |
| `failed` | One or more generators reported failure |
| `aborted` | Benchmark was manually aborted |

### Components

- **SSH Client** (asyncssh) — Remote command execution on cluster nodes
- **Docker Manager** — Container lifecycle via Docker CLI subprocess
- **Kubernetes Manager** — kubeadm orchestration, kr8s node polling
- **Manifest Parser** — k8s.lst file parsing and sequential execution
- **Load Generator Client** (httpx) — HTTP communication with 5 load generators
- **S3 Client** (boto3) — Trial reservation and artifact upload
- **Health Checker** — Configurable retry-based health polling

## Prerequisites

- Python 3.13+
- Docker (with access to the Docker daemon)
- SSH key configured for `ubuntu` user access to cluster nodes
- AWS credentials configured for S3 access
- The `kasbench` Docker network must exist before initialization

## Quick Start

### Local Development

```bash
# Clone and install
git clone https://github.com/kasbench/kasbench-runner.git
cd kasbench-runner

# Install dependencies (requires uv)
uv sync

# Run the application
uv run python main.py
```

### Docker

```bash
# Pull the image
docker pull kasbench/kasbench-runner:latest

# Create the required Docker network
docker network create kasbench

# Run the container
docker run -d \
  --name kasbench-runner \
  --network kasbench \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v ~/.ssh:/root/.ssh:ro \
  -v ~/.aws:/root/.aws:ro \
  kasbench/kasbench-runner:latest
```

### Building the Docker Image

```bash
# Build and push multi-arch image to Docker Hub
./build-and-push.sh

# Build with a specific tag
./build-and-push.sh v0.1.0
```

## Configuration

All configuration is via environment variables. Invalid numeric values fall back to defaults with a WARNING log.

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8080` | Server port |
| `SSH_USER` | `ubuntu` | SSH username for remote nodes |
| `SSH_CONNECT_TIMEOUT` | `30` | SSH connection timeout (seconds) |
| `NODE_READINESS_TIMEOUT_SECONDS` | `300` | Max wait for all nodes Ready (60–1800) |
| `NODE_READINESS_POLL_INTERVAL` | `10` | Node polling interval (seconds) |
| `HEALTH_CHECK_MAX_ATTEMPTS` | `3` | Health check retry count (1–10) |
| `HEALTH_CHECK_INTERVAL_SECONDS` | `5` | Wait between health checks (1–60) |
| `RABBITMQ_IMAGE` | `rabbitmq:4-management` | RabbitMQ Docker image |
| `HTTP_CONNECT_TIMEOUT` | `10` | HTTP client connect timeout (seconds) |
| `HTTP_READ_TIMEOUT` | `30` | HTTP client read timeout (seconds) |
| `MANIFEST_FETCH_TIMEOUT` | `30` | Timeout for fetching k8s.lst files |

## API Reference

Base URL: `http://localhost:8080`

### POST /initialize

Initialize the benchmark environment: reserve S3 trial, configure Kubernetes, deploy manifests, and start load generators.

**Request Body:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `autoscaler` | string | yes | — | Autoscaler identifier |
| `controlPlaneNode` | string | yes | — | Control plane hostname |
| `amdWorkerNodes` | string[] | yes | — | AMD64 worker hostnames |
| `armWorkerNodes` | string[] | yes | — | ARM64 worker hostnames |
| `s3Bucket` | string | yes | — | S3 bucket for artifacts |
| `globecoUrl` | string | yes | — | GlobeCo application URL |
| `runIdentifier` | string | no | `"run001"` | Run identifier |
| `trialIdentifier` | string | no | `"trial001"` | Trial identifier |
| `clusterCidrRange` | string | no | `"10.244.0.0/16"` | Pod network CIDR |
| `kubernetesVersion` | string | no | `"1.36.1"` | Kubernetes version |
| `loadGeneratorImage` | string | no | `"kasbench/kasbench-load-generator:latest"` | Load generator image |
| `runDurationMinutes` | int | no | `5` | Benchmark duration |
| `globecoPort` | int | no | `8080` | GlobeCo port |
| `skipKubernetesInstall` | bool | no | `false` | Skip k8s cluster setup |
| `skipManifestInstall` | bool | no | `false` | Skip manifest deployment |
| `forceManifestInstall` | bool | no | `false` | Continue on manifest errors |

**Responses:** `200` success, `409` already initialized or duplicate trial, `422` validation error, `500` infrastructure failure.

---

### POST /start

Start the benchmark run across all load generators.

**Responses:** `200` with `startTime`, `409` not initialized or already running, `500` generator start failure.

---

### GET /status

Query overall benchmark status and per-generator details.

**Responses:** `200` with status object, `500` if health query fails.

---

### GET /output/{role}

Stream stdout/stderr output from a load generator.

**Path Parameters:** `role` — one of `back-office`, `portfolio-manager`, `trader`, `investor`, `it-operations`

**Responses:** `200` text/plain stream, `400` invalid role, `404` no output, `409` subprocess active, `502` connection failure.

---

### GET /db/{role}

Stream the SQLite database from a load generator.

**Path Parameters:** `role` — one of `back-office`, `portfolio-manager`, `trader`, `investor`, `it-operations`

**Responses:** `200` application/x-sqlite3 stream, `400` invalid role, `404` no database, `409` subprocess active, `502` connection failure.

---

### POST /abort

Abort a running benchmark (best-effort across all generators).

**Responses:** `200` with abort timestamp and per-role results, `409` benchmark not running.

---

### POST /metrics/export

Execute Prometheus range queries for all configured benchmark metrics and upload results as JSON files to S3.

**Request Body (optional):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `overwrite` | bool | `false` | Allow overwriting existing metric files in S3 |
| `interval` | string | `"60s"` | Prometheus duration for `__INTERVAL__` substitution in counter queries |
| `step` | string | `"15s"` | Resolution step for range queries |
| `prometheusPort` | int | `31565` | Prometheus server port (1–65535) |

**Success Response (200):**

```json
{
  "message": "Metrics collected and uploaded successfully",
  "metricsUploaded": 54,
  "metricsTotal": 54,
  "s3Prefix": "run001/trial001/metrics/",
  "timestamp": "2026-06-10T14:40:00.000000+00:00"
}
```

**Error Responses:**

| Status | Error | Condition |
|--------|-------|-----------|
| `409` | `benchmark_not_completed` | Benchmark status is `not-initialized`, `not-started`, or `running` |
| `409` | `metrics_already_exist` | `overwrite` is false and metric files already exist in S3 |
| `207` | Partial success | One or more queries or uploads failed; response includes error details |
| `500` | `missing_time_bounds` | Benchmark start_time or end_time is not available |

**Allowed States:** `success`, `failed`, `aborted`

---

### POST /prometheus/tsdb/export

Trigger a Prometheus TSDB snapshot, copy it from the prometheus-server pod, and upload to S3.

**Request Body (optional):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prometheusPort` | int | `31565` | Prometheus server port (1–65535) |

**Success Response (200):**

```json
{
  "s3Path": "run001/trial001/tsdb-snapshots",
  "timestamp": "2026-06-10T14:42:00.000000+00:00"
}
```

**Error Responses:**

| Status | Error | Condition |
|--------|-------|-----------|
| `409` | `not_initialized` | Benchmark has not been initialized |
| `502` | `prometheus_api_failed` | Prometheus snapshot API call failed or timed out (30s) |
| `500` | `pod_not_found` | No prometheus-server pod found in `monitoring` namespace |
| `500` | `copy_failed` | Snapshot copy from pod failed |
| `500` | `s3_upload_failed` | S3 upload failed |

**Allowed States:** Any state except `not-initialized`

---

### POST /output/export

Export output from all five load generators to S3.

### POST /output/export/{role}

Export output from a single load generator to S3.

**Path Parameters:** `role` — one of `back-office`, `portfolio-manager`, `trader`, `investor`, `it-operations`

**S3 Path:** `{s3Bucket}/{runIdentifier}/{trialIdentifier}/output/{role}-output.txt`

**Success Response (200):**

```json
{
  "message": "Output exported successfully",
  "filesExported": 5,
  "s3Prefix": "run001/trial001/output/",
  "timestamp": "2026-06-10T14:43:00.000000+00:00"
}
```

**Partial Success Response (207):**

```json
{
  "message": "Partial export completed",
  "filesExported": 3,
  "results": [
    {"role": "back-office", "status": "success", "s3Key": "run001/trial001/output/back-office-output.txt"},
    {"role": "trader", "status": "failed", "error": "Connection refused"}
  ],
  "timestamp": "2026-06-10T14:43:00.000000+00:00"
}
```

**Error Responses:**

| Status | Error | Condition |
|--------|-------|-----------|
| `400` | `invalid_role` | Role path parameter is not one of the 5 valid roles |
| `409` | `not_initialized` | Benchmark has not been initialized |
| `502` | `connection_failed` | Load generator connection failed or timed out |
| `500` | `s3_upload_failed` | S3 upload failed |

**Allowed States:** Any state except `not-initialized`

---

### POST /db/export

Export databases from all five load generators to S3.

### POST /db/export/{role}

Export a database from a single load generator to S3.

**Path Parameters:** `role` — one of `back-office`, `portfolio-manager`, `trader`, `investor`, `it-operations`

**S3 Path:** `{s3Bucket}/{runIdentifier}/{trialIdentifier}/db/{role}.db`

**Success Response (200):**

```json
{
  "message": "Database export completed successfully",
  "filesExported": 5,
  "results": [
    {"role": "back-office", "status": "success", "s3Key": "run001/trial001/db/back-office.db"},
    {"role": "portfolio-manager", "status": "success", "s3Key": "run001/trial001/db/portfolio-manager.db"},
    {"role": "trader", "status": "success", "s3Key": "run001/trial001/db/trader.db"},
    {"role": "investor", "status": "success", "s3Key": "run001/trial001/db/investor.db"},
    {"role": "it-operations", "status": "success", "s3Key": "run001/trial001/db/it-operations.db"}
  ],
  "timestamp": "2026-06-10T14:44:00.000000+00:00"
}
```

**Error Responses:**

| Status | Error | Condition |
|--------|-------|-----------|
| `400` | `invalid_role` | Role path parameter is not one of the 5 valid roles |
| `409` | `not_initialized` | Benchmark has not been initialized |
| `502` | `load_generator_failed` | Load generator returned non-200 or connection timed out (10s) |
| `500` | `s3_upload_failed` | S3 upload failed |

**Allowed States:** Any state except `not-initialized`

---

### POST /metadata/export

Export a comprehensive metadata document (run_details.json) to S3 capturing the full benchmark configuration and state.

**S3 Path:** `{s3Bucket}/{runIdentifier}/{trialIdentifier}/run_details.json`

**JSON Document Fields:**

| Field | Description |
|-------|-------------|
| `timestamp` | ISO 8601 UTC generation time |
| `environment` | All 12 RunnerConfig fields (HOST, PORT, SSH_USER, SSH_CONNECT_TIMEOUT, NODE_READINESS_TIMEOUT_SECONDS, NODE_READINESS_POLL_INTERVAL, HEALTH_CHECK_MAX_ATTEMPTS, HEALTH_CHECK_INTERVAL_SECONDS, RABBITMQ_IMAGE, HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT, MANIFEST_FETCH_TIMEOUT) |
| `initialization` | All 15 initialization fields (autoscaler, controlPlaneNode, amdWorkerNodes, armWorkerNodes, s3Bucket, globecoUrl, runIdentifier, trialIdentifier, clusterCidrRange, kubernetesVersion, loadGeneratorImage, runDurationMinutes, globecoPort, skipKubernetesInstall, skipManifestInstall, forceManifestInstall) |
| `roles` | Per-role parameters (base_load_intensity, base_delay_percentage, spawn_rate) for all 5 roles |
| `manifests` | Kubernetes manifest repositories with owner, repo, and tag fields |
| `status` | Full status response equivalent to GET /status (overall status, start_time, end_time, load_generators) |

**Success Response (200):**

```json
{
  "s3Key": "run001/trial001/run_details.json",
  "timestamp": "2026-06-10T14:45:00.000000+00:00"
}
```

**Error Responses:**

| Status | Error | Condition |
|--------|-------|-----------|
| `409` | `not_initialized` | Benchmark has not been initialized |
| `500` | `s3_upload_failed` | S3 upload failed |

**Allowed States:** Any state except `not-initialized`

---

### POST /shutdown

Delete Kubernetes namespaces with PVCs to cleanly release storage volumes before cluster destruction.

**Namespaces Deleted (in order):** `globeco`, `elasticsearch`, `observability`, `monitoring`

Each namespace deletion waits up to 60 seconds. If a deletion fails or times out, processing continues with the next namespace.

**Success Response (200):**

```json
{
  "results": [
    {"namespace": "globeco", "status": "success"},
    {"namespace": "elasticsearch", "status": "success"},
    {"namespace": "observability", "status": "success"},
    {"namespace": "monitoring", "status": "success"}
  ],
  "timestamp": "2026-06-10T14:46:00.000000+00:00"
}
```

**Error Responses:**

| Status | Error | Condition |
|--------|-------|-----------|
| `409` | `not_initialized` | Benchmark has not been initialized |
| `409` | `benchmark_running` | Benchmark is currently running; shutdown is not permitted |

**Allowed States:** `not-started`, `success`, `failed`, `aborted`

---

## Usage Examples

The following examples assume the Runner is accessible at `http://localhost:8080`.

### Launch the Runner

```bash
# Create the Docker network
docker network create kasbench

# Start the runner container
docker run -d \
  --name kasbench-runner \
  --network kasbench \
  -p 8080:8080 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v ~/.ssh:/root/.ssh:ro \
  -v ~/.aws:/root/.aws:ro \
  -e NODE_READINESS_TIMEOUT_SECONDS=600 \
  kasbench/kasbench-runner:latest

# Wait for it to start
sleep 3
```

### POST /initialize

```bash
curl -s -X POST http://localhost:8080/initialize \
  -H "Content-Type: application/json" \
  -d '{
    "autoscaler": "karpenter",
    "controlPlaneNode": "10.0.1.10",
    "amdWorkerNodes": ["10.0.1.20", "10.0.1.21"],
    "armWorkerNodes": ["10.0.1.30"],
    "s3Bucket": "kasbench-results",
    "globecoUrl": "http://globeco.globeco.svc.cluster.local",
    "runIdentifier": "run001",
    "trialIdentifier": "trial001",
    "runDurationMinutes": 10,
    "skipKubernetesInstall": false,
    "skipManifestInstall": false
  }' | jq .
```

**Expected response (200):**
```json
{
  "message": "Initialization complete",
  "status": "not-started"
}
```

### POST /initialize (skip k8s and manifests for testing)

```bash
curl -s -X POST http://localhost:8080/initialize \
  -H "Content-Type: application/json" \
  -d '{
    "autoscaler": "karpenter",
    "controlPlaneNode": "10.0.1.10",
    "amdWorkerNodes": ["10.0.1.20"],
    "armWorkerNodes": ["10.0.1.30"],
    "s3Bucket": "kasbench-results",
    "globecoUrl": "http://globeco.globeco.svc.cluster.local",
    "skipKubernetesInstall": true,
    "skipManifestInstall": true
  }' | jq .
```

### POST /start

```bash
curl -s -X POST http://localhost:8080/start | jq .
```

**Expected response (200):**
```json
{
  "startTime": "2026-06-10T14:30:00.123456+00:00"
}
```

### GET /status

```bash
curl -s http://localhost:8080/status | jq .
```

**Expected response (200):**
```json
{
  "status": "running",
  "startTime": "2026-06-10T14:30:00.123456+00:00",
  "endTime": null,
  "loadGenerators": [
    {"role": "back-office", "status": "running", "startTime": "2026-06-10T14:30:00.200000+00:00", "endTime": null},
    {"role": "portfolio-manager", "status": "running", "startTime": "2026-06-10T14:30:00.210000+00:00", "endTime": null},
    {"role": "trader", "status": "running", "startTime": "2026-06-10T14:30:00.220000+00:00", "endTime": null},
    {"role": "investor", "status": "running", "startTime": "2026-06-10T14:30:00.230000+00:00", "endTime": null},
    {"role": "it-operations", "status": "running", "startTime": "2026-06-10T14:30:00.240000+00:00", "endTime": null}
  ]
}
```

### GET /status (before initialization)

```bash
curl -s http://localhost:8080/status | jq .
```

**Expected response (200):**
```json
{
  "status": "not-initialized",
  "startTime": null,
  "endTime": null,
  "loadGenerators": []
}
```

### GET /output/{role}

```bash
# Download output from the trader load generator
curl -s http://localhost:8080/output/trader > trader-output.txt

# Stream and display in real-time
curl -s http://localhost:8080/output/back-office
```

**Expected response (200):** Plain text stream of stdout/stderr.

### GET /output/{role} — invalid role

```bash
curl -s http://localhost:8080/output/invalid-role | jq .
```

**Expected response (400):**
```json
{
  "error": "invalid_role",
  "message": "Invalid role: 'invalid-role'",
  "context": {
    "invalid_value": "invalid-role",
    "valid_roles": ["back-office", "portfolio-manager", "trader", "investor", "it-operations"]
  },
  "timestamp": "2026-06-10T14:35:00.000000+00:00"
}
```

### GET /db/{role}

```bash
# Download SQLite database from investor load generator
curl -s http://localhost:8080/db/investor > investor.db

# Inspect the downloaded database
sqlite3 investor.db ".tables"
```

### POST /abort

```bash
curl -s -X POST http://localhost:8080/abort | jq .
```

**Expected response (200):**
```json
{
  "abortTime": "2026-06-10T14:32:00.000000+00:00",
  "results": {
    "back-office": "success",
    "portfolio-manager": "success",
    "trader": "success",
    "investor": "success",
    "it-operations": "success"
  }
}
```

### POST /abort — not running

```bash
curl -s -X POST http://localhost:8080/abort | jq .
```

**Expected response (409):**
```json
{
  "error": "benchmark_not_running",
  "message": "Cannot abort: benchmark is not currently running",
  "context": {"current_status": "not-initialized"},
  "timestamp": "2026-06-10T14:32:00.000000+00:00"
}
```

### POST /metrics/export — default invocation

```bash
curl -s -X POST http://localhost:8080/metrics/export | jq .
```

**Expected response (200):**
```json
{
  "message": "Metrics collected and uploaded successfully",
  "metricsUploaded": 54,
  "metricsTotal": 54,
  "s3Prefix": "run001/trial001/metrics/",
  "timestamp": "2026-06-10T14:40:00.000000+00:00"
}
```

### POST /metrics/export — custom parameters with Prometheus port

```bash
curl -s -X POST http://localhost:8080/metrics/export \
  -H "Content-Type: application/json" \
  -d '{
    "overwrite": true,
    "interval": "30s",
    "step": "10s",
    "prometheusPort": 9090
  }' | jq .
```

**Expected response (200):**
```json
{
  "message": "Metrics collected and uploaded successfully",
  "metricsUploaded": 54,
  "metricsTotal": 54,
  "s3Prefix": "run001/trial001/metrics/",
  "timestamp": "2026-06-10T14:40:00.000000+00:00"
}
```

### POST /metrics/export — benchmark not completed

```bash
curl -s -X POST http://localhost:8080/metrics/export | jq .
```

**Expected response (409):**
```json
{
  "error": "benchmark_not_completed",
  "message": "Metrics collection is only available after the benchmark has completed",
  "context": {"current_status": "running"},
  "timestamp": "2026-06-10T14:35:00.000000+00:00"
}
```

### POST /prometheus/tsdb/export

```bash
curl -s -X POST http://localhost:8080/prometheus/tsdb/export | jq .
```

**Expected response (200):**
```json
{
  "s3Path": "run001/trial001/tsdb-snapshots",
  "timestamp": "2026-06-10T14:42:00.000000+00:00"
}
```

### POST /output/export

```bash
curl -s -X POST http://localhost:8080/output/export | jq .
```

**Expected response (200):**
```json
{
  "message": "Output exported successfully",
  "filesExported": 5,
  "s3Prefix": "run001/trial001/output/",
  "timestamp": "2026-06-10T14:43:00.000000+00:00"
}
```

### POST /output/export/{role}

```bash
curl -s -X POST http://localhost:8080/output/export/trader | jq .
```

**Expected response (200):**
```json
{
  "message": "Output exported successfully",
  "filesExported": 1,
  "s3Prefix": "run001/trial001/output/",
  "timestamp": "2026-06-10T14:43:00.000000+00:00"
}
```

### POST /db/export

```bash
curl -s -X POST http://localhost:8080/db/export | jq .
```

**Expected response (200):**
```json
{
  "message": "Database export completed successfully",
  "filesExported": 5,
  "results": [
    {"role": "back-office", "status": "success", "s3Key": "run001/trial001/db/back-office.db"},
    {"role": "portfolio-manager", "status": "success", "s3Key": "run001/trial001/db/portfolio-manager.db"},
    {"role": "trader", "status": "success", "s3Key": "run001/trial001/db/trader.db"},
    {"role": "investor", "status": "success", "s3Key": "run001/trial001/db/investor.db"},
    {"role": "it-operations", "status": "success", "s3Key": "run001/trial001/db/it-operations.db"}
  ],
  "timestamp": "2026-06-10T14:44:00.000000+00:00"
}
```

### POST /metadata/export

```bash
curl -s -X POST http://localhost:8080/metadata/export | jq .
```

**Expected response (200):**
```json
{
  "s3Key": "run001/trial001/run_details.json",
  "timestamp": "2026-06-10T14:45:00.000000+00:00"
}
```

### POST /shutdown

```bash
curl -s -X POST http://localhost:8080/shutdown | jq .
```

**Expected response (200):**
```json
{
  "results": [
    {"namespace": "globeco", "status": "success"},
    {"namespace": "elasticsearch", "status": "success"},
    {"namespace": "observability", "status": "success"},
    {"namespace": "monitoring", "status": "success"}
  ],
  "timestamp": "2026-06-10T14:46:00.000000+00:00"
}
```

### Full Lifecycle Script

```bash
#!/usr/bin/env bash
set -euo pipefail

RUNNER="http://localhost:8080"

echo "=== 1. Check initial status ==="
curl -s "$RUNNER/status" | jq .

echo ""
echo "=== 2. Initialize ==="
curl -s -X POST "$RUNNER/initialize" \
  -H "Content-Type: application/json" \
  -d '{
    "autoscaler": "karpenter",
    "controlPlaneNode": "10.0.1.10",
    "amdWorkerNodes": ["10.0.1.20", "10.0.1.21"],
    "armWorkerNodes": ["10.0.1.30"],
    "s3Bucket": "kasbench-results",
    "globecoUrl": "http://globeco.globeco.svc.cluster.local",
    "runDurationMinutes": 5
  }' | jq .

echo ""
echo "=== 3. Start benchmark ==="
curl -s -X POST "$RUNNER/start" | jq .

echo ""
echo "=== 4. Poll status until complete ==="
while true; do
  STATUS=$(curl -s "$RUNNER/status" | jq -r '.status')
  echo "  Status: $STATUS"
  if [[ "$STATUS" == "success" || "$STATUS" == "failed" || "$STATUS" == "aborted" ]]; then
    break
  fi
  sleep 30
done

echo ""
echo "=== 5. Final status ==="
curl -s "$RUNNER/status" | jq .

echo ""
echo "=== 6. Collect metrics ==="
curl -s -X POST "$RUNNER/metrics/export" | jq .

echo ""
echo "=== 7. Export Prometheus TSDB snapshot ==="
curl -s -X POST "$RUNNER/prometheus/tsdb/export" | jq .

echo ""
echo "=== 8. Export outputs to S3 ==="
curl -s -X POST "$RUNNER/output/export" | jq .

echo ""
echo "=== 9. Export databases to S3 ==="
curl -s -X POST "$RUNNER/db/export" | jq .

echo ""
echo "=== 10. Export metadata ==="
curl -s -X POST "$RUNNER/metadata/export" | jq .

echo ""
echo "=== 11. Shutdown namespaces ==="
curl -s -X POST "$RUNNER/shutdown" | jq .

echo ""
echo "=== Done ==="
```

## Load Generator Roles

The Runner manages five load generator containers, each simulating a different user profile:

| Role | Host Port | Load Intensity | Spawn Rate | Description |
|------|-----------|----------------|------------|-------------|
| `back-office` | 8081 | 100 | 10 | Back-office operations |
| `portfolio-manager` | 8082 | 100 | 10 | Portfolio management |
| `trader` | 8083 | 100 | 10 | Trading operations |
| `investor` | 8084 | 10 | 10 | Investor activity |
| `it-operations` | 8085 | 100 | 1 | IT operations/monitoring |

## Error Handling

All errors return a consistent JSON structure:

```json
{
  "error": "operation_that_failed",
  "message": "Human-readable description",
  "context": {
    "operation-specific": "diagnostic fields"
  },
  "timestamp": "2026-06-10T14:30:00.000000+00:00"
}
```

Error responses never obfuscate or redact details — maximum diagnostic information is provided to aid debugging.

## Development

```bash
# Install all dependencies (including dev)
uv sync

# Run tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=kasbench_runner

# Type checking (if mypy is added)
uv run mypy src/

# Run locally
uv run python main.py
```

## Project Structure

```
kasbench-runner/
├── main.py                          # Application entrypoint
├── Dockerfile                       # Multi-stage Docker build
├── build-and-push.sh               # Multi-arch build & push script
├── pyproject.toml                   # Dependencies and project metadata
├── uv.lock                          # Locked dependency versions
├── src/kasbench_runner/
│   ├── app.py                       # FastAPI factory + route registration
│   ├── config.py                    # Environment-based configuration
│   ├── errors.py                    # Exception hierarchy + error builder
│   ├── logging.py                   # structlog JSON configuration
│   ├── models/
│   │   ├── requests.py              # InitializeRequest model
│   │   ├── responses.py             # API response models
│   │   └── state.py                 # BenchmarkState + BenchmarkStatus
│   ├── routes/
│   │   ├── initialize.py            # POST /initialize
│   │   ├── start.py                 # POST /start
│   │   ├── status.py                # GET /status
│   │   ├── output.py                # GET /output/{role}, POST /output/export, POST /output/export/{role}
│   │   ├── db.py                    # GET /db/{role}, POST /db/export, POST /db/export/{role}
│   │   ├── abort.py                 # POST /abort
│   │   ├── metrics.py               # POST /metrics/export
│   │   ├── prometheus_tsdb.py       # POST /prometheus/tsdb/export
│   │   ├── metadata.py              # POST /metadata/export
│   │   └── shutdown.py              # POST /shutdown
│   └── services/
│       ├── ssh_client.py            # Async SSH via asyncssh
│       ├── docker_manager.py        # Docker CLI operations
│       ├── kubernetes_manager.py    # Cluster setup orchestration
│       ├── manifest_parser.py       # k8s.lst parsing + execution
│       ├── load_generator_client.py # HTTP client for generators
│       ├── s3_client.py             # S3 reservation + upload
│       ├── metrics_config.py        # Prometheus metric definitions
│       ├── prometheus_client.py     # Prometheus range query client
│       └── health_checker.py        # Retry-based health polling
└── tests/                           # Test suite
```

## License

See [LICENSE](LICENSE) for details.
