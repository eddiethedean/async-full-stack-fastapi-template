#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
export DOMAIN=discovery.example.test
export BACKEND_IMAGE="${BACKEND_IMAGE:-backend:latest}"
run_dir=$(mktemp -d)
project="asf-discovery-$(basename "$run_dir" | tr '[:upper:]' '[:lower:]' | tr '.' '-')"
trap 'rm -f "$run_dir/compose.fixture.yml"; rmdir "$run_dir"' EXIT
cat > "$run_dir/compose.fixture.yml" <<'YAML'
services:
  proxy:
    ports: !override
      - "127.0.0.1::${DISCOVERY_TEST_PORT}"
  backend:
    # The HTTP fixture answers routing requests without a database.
    healthcheck:
      disable: true
YAML

wait_for_status() {
    local address=$1 expected=$2 actual
    for _ in {1..25}; do
        # The deployment fixture uses Traefik's local, self-signed certificate.
        actual=$(curl --noproxy '*' --silent --insecure --max-time 2 \
            --header "Host: $DOMAIN" --output /dev/null --write-out '%{http_code}' "$address") || true
        if [[ "$actual" == "$expected" ]]; then
            return
        fi
        sleep 1
    done
    printf 'Expected HTTP %s from %s, received %s\n' "$expected" "$address" "$actual" >&2
    return 1
}

check_stack() (
    local variant=$1 override=$2 scheme=$3 port=$4
    local stack="$project-$variant"
    local -a compose=(docker compose --project-name "$stack" -f compose.yml -f "$override" -f "$run_dir/compose.fixture.yml")
    local -a fixture=(run --detach --no-deps --pull never --name "$stack-backend")
    export DISCOVERY_TEST_PORT="$port"
    if [[ "$scheme" == https ]]; then
        # Exercise TLS routing with a disposable certificate and no ACME request.
        fixture+=(--label traefik.http.routers.backend-https.tls.certresolver=)
    fi

    "${compose[@]}" config --format json | python3 -c '
import json
import sys

config = json.load(sys.stdin)
services = config["services"]
proxy = services["proxy"]
assert not any(mount["target"] == "/var/run/docker.sock" for mount in proxy.get("volumes", [])), "Traefik mounts the Docker socket"
assert "--providers.docker.endpoint=tcp://docker-discovery:2375" in proxy["command"]
assert config["networks"]["discovery"]["internal"]
assert {name for name, service in services.items() if "discovery" in service.get("networks", {})} == {"proxy", "docker-discovery"}
assert set(services["docker-discovery"]["networks"]) == {"discovery"}
assert not services["docker-discovery"].get("ports")
'

    trap '"${compose[@]}" down --volumes --remove-orphans' EXIT
    "${compose[@]}" up -d --wait proxy
    local address
    address="$scheme://$("${compose[@]}" port proxy "$port")"
    wait_for_status "$address" 404

    docker run --rm -i --pull never --network "${stack}_discovery" "$BACKEND_IMAGE" python - <<'PY'
from urllib.error import HTTPError
from urllib.request import Request, urlopen

origin = "http://docker-discovery:2375"
with urlopen(origin + "/version", timeout=5) as response:
    assert response.status == 200

# The POST targets the daemon's harmless ping endpoint even if the guard fails.
for method, path in (("POST", "/_ping"), ("GET", "/info")):
    try:
        with urlopen(Request(origin + path, method=method), timeout=5) as response:
            raise AssertionError(f"Unexpected {response.status} for {method} {path}")
    except HTTPError as error:
        assert error.code == 403, (method, path, error.code)
PY

    "${compose[@]}" "${fixture[@]}" backend python -m http.server --directory /tmp 8000
    wait_for_status "$address" 200
    docker stop --timeout 1 "$stack-backend"
    wait_for_status "$address" 404
    docker start "$stack-backend"
    wait_for_status "$address" 200
    printf '%s discovery and routing checks passed\n' "$variant"
)

check_stack dev compose.override.yml http 80
check_stack deploy compose.deploy.yml https 443
