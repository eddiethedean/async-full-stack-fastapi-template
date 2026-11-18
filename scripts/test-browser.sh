#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
project="asf-browser-$(date +%s)-$$"
compose=(docker compose --project-name "$project" -f compose.yml -f compose.test.yml)
cleanup() {
    trap '' INT TERM
    "${compose[@]}" down --volumes --remove-orphans
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${compose[@]}" build backend playwright
"${compose[@]}" run -T --rm backend bash scripts/prestart.sh </dev/null

if [[ "${1:-}" == "--ui" ]]; then
    shift
    echo "Open http://127.0.0.1:9323 for Playwright UI."
    "${compose[@]}" run -T --rm --publish 127.0.0.1:9323:9323 playwright \
        bunx playwright test --ui --ui-host=0.0.0.0 --ui-port=9323 "$@"
else
    "${compose[@]}" run -T --rm playwright bunx playwright test "$@"
fi
