# syntax=docker/dockerfile:1

########################################
# Builder stage: install Python deps with uv
########################################
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

# Build a self-contained virtualenv at /app/.venv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (cached layer) using only the lock/metadata files.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the source and install the project itself into the venv.
# --no-editable installs a real copy into site-packages so the runtime stage
# only needs the venv (no source tree required).
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

########################################
# Runtime stage: python + tofu + aws + ssh
########################################
FROM python:3.13-slim-bookworm AS runtime

# TARGETARCH is provided automatically by buildx: "amd64" or "arm64".
ARG TARGETARCH

# Pin the tools we bundle. Override at build time with --build-arg if needed.
ARG TOFU_VERSION=1.8.8
ARG AWSCLI_VERSION=2.17.0

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# Runtime OS packages:
#   - openssh-client: ssh/scp used by initialize-runner
#   - ca-certificates, curl, unzip: fetch and unpack tofu / aws CLI
#   - git: benchmark-infrastructure tooling / repo operations
#   - groff, less: required by the AWS CLI at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client \
        ca-certificates \
        curl \
        unzip \
        git \
        groff \
        less \
    && rm -rf /var/lib/apt/lists/*

# Install OpenTofu (multi-arch: amd64 / arm64).
RUN set -eux; \
    curl -fsSL -o /tmp/tofu.zip \
        "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_linux_${TARGETARCH}.zip"; \
    unzip -o /tmp/tofu.zip -d /usr/local/bin tofu; \
    chmod +x /usr/local/bin/tofu; \
    rm -f /tmp/tofu.zip; \
    tofu version

# Install AWS CLI v2 (multi-arch: x86_64 / aarch64).
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) AWS_ARCH="x86_64" ;; \
        arm64) AWS_ARCH="aarch64" ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/awscliv2.zip \
        "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}-${AWSCLI_VERSION}.zip"; \
    unzip -q /tmp/awscliv2.zip -d /tmp; \
    /tmp/aws/install; \
    rm -rf /tmp/aws /tmp/awscliv2.zip; \
    aws --version

WORKDIR /app

# Copy the prebuilt virtualenv from the builder stage.
COPY --from=builder /app/.venv /app/.venv

# Conventional mount point for benchmark artifacts. Mount a host volume here and
# pass `--working-directory /data/benchmarks` to the CLI so results persist.
RUN mkdir -p /data/benchmarks
VOLUME ["/data/benchmarks"]

# Image provenance. A container cannot inspect its own image name/tags/ID without
# the Docker daemon, so the host injects these at `docker run` time (e.g. from
# `docker inspect`). They default to empty; the controller reads them and records
# them in its structured logs. See the README "Image provenance" section.
ENV KASBENCH_IMAGE_NAME="" \
    KASBENCH_IMAGE_TAGS="" \
    KASBENCH_IMAGE_ID=""

# The venv exposes the `kasbench` console script on PATH.
ENTRYPOINT ["kasbench"]
CMD ["--help"]
