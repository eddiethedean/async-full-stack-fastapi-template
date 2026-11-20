#!/usr/bin/env bash
set -euo pipefail

frontend_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT

cp "$frontend_dir/biome.json" "$frontend_dir/package.json" "$fixture/"
export PATH="$frontend_dir/node_modules/.bin:$frontend_dir/../node_modules/.bin:$PATH"

trace_dir="$fixture/test-results/.playwright-artifacts-1/traces/resources"
mkdir -p "$trace_dir" "$fixture/src"
trace_css='a { -webkit-text-decoration: inherit; -webkit-text-decoration: inherit; }'
printf '%s\n' "$trace_css" > "$trace_dir/trace.css"
printf '%s\n' 'a{color:red}' > "$fixture/src/source.css"

bun run --cwd "$fixture" lint
cmp "$trace_dir/trace.css" <(printf '%s\n' "$trace_css")
grep -qFx '  color: red;' "$fixture/src/source.css"
