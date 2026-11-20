#!/usr/bin/env bash
set -euo pipefail

compose=(docker compose "$@")
# Drain the previous worker before the migration starts cancelling leases.
"${compose[@]}" stop email-worker
"${compose[@]}" run --rm --no-deps --pull never backend bash scripts/prestart.sh
