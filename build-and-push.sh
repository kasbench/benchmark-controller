#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="kasbench/kasbench-controller"
TAG="${1:-latest}"

echo "==> Building and pushing ${IMAGE_NAME}:${TAG} for linux/amd64 and linux/arm64"

# Ensure buildx builder exists with multi-platform support
BUILDER_NAME="kasbench-multiplatform"
if ! docker buildx inspect "${BUILDER_NAME}" &>/dev/null; then
    echo "==> Creating buildx builder: ${BUILDER_NAME}"
    docker buildx create --name "${BUILDER_NAME}" --use --driver docker-container
else
    docker buildx use "${BUILDER_NAME}"
fi

# Build and push multi-arch image
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --tag "${IMAGE_NAME}:${TAG}" \
    --tag "${IMAGE_NAME}:latest" \
    --push \
    .

echo "==> Done. Pushed ${IMAGE_NAME}:${TAG} (linux/amd64, linux/arm64)"
