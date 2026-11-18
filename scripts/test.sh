#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
repo_dir="$PWD"
run_dir=$(mktemp -d)
project="asf-test-$(basename "$run_dir" | tr '[:upper:]' '[:lower:]' | tr '.' '-')"
compose=(docker compose --project-name "$project" -f compose.yml -f compose.test.yml)
cleanup() {
    cd "$repo_dir"
    "${compose[@]}" down --volumes --remove-orphans
    rmdir "$run_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${compose[@]}" up -d --wait db mailpit
db_port=$("${compose[@]}" port db 5432)
smtp_port=$("${compose[@]}" port mailpit 1025)
export TEST_DATABASE_URL="postgresql://postgres:test-only-postgres@${db_port}/asf_test_suite"
export SMTP_HOST=127.0.0.1 SMTP_PORT="${smtp_port##*:}" SMTP_TLS=false SMTP_SSL=false
export SMTP_USER="" SMTP_PASSWORD="" SENTRY_DSN=""
cd backend
uv run --locked bash scripts/test.sh "$@"
