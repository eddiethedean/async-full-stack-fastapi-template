#!/usr/bin/env bash
set -euo pipefail

image_reference=${1:?Pass the Docker image ID or reference to scan}
image_id=$(docker image inspect --format '{{.Id}}' "$image_reference")
scan_dir=$(mktemp -d)
trap 'rm -rf "$scan_dir"' EXIT

docker save --output "$scan_dir/backend.tar" "$image_id"
docker run --rm --volume "$scan_dir:/scan:ro" \
    aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb \
    image --input /scan/backend.tar --scanners vuln --severity HIGH,CRITICAL \
    --ignore-unfixed --exit-code 1
