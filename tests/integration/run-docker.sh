#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
PACKAGE=$(find "$ROOT/build" -maxdepth 1 -type f -name 'dfr_*_all.deb' -print -quit)
if [[ -z "$PACKAGE" ]]; then
  echo "No Debian package found under $ROOT/build" >&2
  exit 1
fi

PACKAGE_ARG=${PACKAGE#"$ROOT/"}
SUFFIX=${GITHUB_RUN_ID:-local}-${RANDOM}
IMAGE="dfr-integration:$SUFFIX"
NETWORK="dfr-integration-$SUFFIX"
NODES=(node-a node-b node-c)
CONTAINERS=(dfr-node-a dfr-node-b dfr-node-c)
TEMP_DIR=$(mktemp -d)

cleanup() {
  local status="$1"
  trap - EXIT
  if ((status != 0)); then
    for container in "${CONTAINERS[@]}"; do
      docker logs "$container" 2>&1 || true
      docker exec "$container" sh -c 'cat /tmp/dfrd.log /tmp/dfr-web.log 2>/dev/null || true' 2>&1 || true
    done
  fi
  docker rm -f "${CONTAINERS[@]}" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  docker image rm "$IMAGE" >/dev/null 2>&1 || true
  rm -rf -- "$TEMP_DIR"
  exit "$status"
}
trap 'cleanup "$?"' EXIT

docker build --tag "$IMAGE" --file "$ROOT/tests/integration/Dockerfile" \
  --build-arg "DEB_PACKAGE=$PACKAGE_ARG" "$ROOT"
docker network create "$NETWORK" >/dev/null

for index in "${!NODES[@]}"; do
  docker run --detach --name "${CONTAINERS[$index]}" \
    --hostname "${NODES[$index]}" --network "$NETWORK" \
    --network-alias "${NODES[$index]}" "$IMAGE" >/dev/null
done

write_config() {
  local node="$1" peer_a peer_b container
  container="dfr-$node"
  case "$node" in
    node-a) peer_a=node-b; peer_b=node-c ;;
    node-b) peer_a=node-a; peer_b=node-c ;;
    node-c) peer_a=node-a; peer_b=node-b ;;
    *) return 2 ;;
  esac
  docker exec -i "$container" sh -c 'cat > /etc/dfr/dfr.conf' <<EOF
[node]
id = $node
root = /srv/dfr/data
state_db = /var/lib/dfr/state.db

[sync]
debounce_ms = 200
reconcile_interval_s = 2
max_hash_size_mb = 100
trash_retention_days = 7
exclude = *.swp, *.tmp, .~lock.*

[peers]
$peer_a = dfr@$peer_a
$peer_b = dfr@$peer_b

[ssh]
key = /var/lib/dfr/.ssh/id_ed25519

[logging]
level = debug
file = /var/log/dfr/dfr.log

[web]
bind = 127.0.0.1:8080
socket = /run/dfr/dfr.sock
EOF
  docker exec "$container" chown root:dfr /etc/dfr/dfr.conf
  docker exec "$container" chmod 0640 /etc/dfr/dfr.conf
  docker exec "$container" chown -R dfr:dfr /srv/dfr/data /var/lib/dfr /var/log/dfr
}

for node in "${NODES[@]}"; do write_config "$node"; done

for container in "${CONTAINERS[@]}"; do
  docker exec "$container" test -s /var/lib/dfr/.ssh/id_ed25519.pub
  docker exec "$container" chown dfr:dfr /var/lib/dfr/.ssh/id_ed25519
  docker exec "$container" chmod 0600 /var/lib/dfr/.ssh/id_ed25519
done

for container in "${CONTAINERS[@]}"; do
  for ((attempt = 1; attempt <= 30; attempt++)); do
    if docker exec "$container" sh -c \
      'ssh-keyscan -T 2 node-a node-b node-c 2>/dev/null > /var/lib/dfr/.ssh/known_hosts && grep -q "^node-a " /var/lib/dfr/.ssh/known_hosts && grep -q "^node-b " /var/lib/dfr/.ssh/known_hosts && grep -q "^node-c " /var/lib/dfr/.ssh/known_hosts'; then
      break
    fi
    if ((attempt == 30)); then
      echo "Could not collect SSH host keys in $container" >&2
      exit 1
    fi
    sleep 0.1
  done
  docker exec "$container" chown dfr:dfr /var/lib/dfr/.ssh/known_hosts
  docker exec "$container" chmod 0600 /var/lib/dfr/.ssh/known_hosts
done

: > "$TEMP_DIR/authorized_keys"
for container in "${CONTAINERS[@]}"; do
  key=$(docker exec "$container" cat /var/lib/dfr/.ssh/id_ed25519.pub)
  read -r key_type key_data _ <<<"$key"
  printf 'restrict,command="/usr/lib/dfr/ssh-gate" %s %s dfr-ci\n' \
    "$key_type" "$key_data" >> "$TEMP_DIR/authorized_keys"
done

for container in "${CONTAINERS[@]}"; do
  docker exec -i "$container" sh -c \
    'cat > /var/lib/dfr/.ssh/authorized_keys && chown dfr:dfr /var/lib/dfr/.ssh/authorized_keys && chmod 0600 /var/lib/dfr/.ssh/authorized_keys' \
    < "$TEMP_DIR/authorized_keys"
done

bootstrap_content='DFR bootstrap before the peers start'
printf '%s\n' "$bootstrap_content" | docker exec -i dfr-node-a sh -c \
  'cat > /srv/dfr/data/ci-bootstrap.txt && chown dfr:dfr /srv/dfr/data/ci-bootstrap.txt'

start_daemon() {
  local container="$1"
  docker exec "$container" su -s /bin/sh dfr -c \
    'nohup /usr/sbin/dfrd --config /etc/dfr/dfr.conf >/tmp/dfrd.log 2>&1 </dev/null &'
}

start_web() {
  local container="$1"
  docker exec "$container" su -s /bin/sh dfr -c \
    'nohup /usr/bin/dfr-web --config /etc/dfr/dfr.conf >/tmp/dfr-web.log 2>&1 </dev/null &'
}

start_daemon dfr-node-a
sleep 0.5
start_daemon dfr-node-b

bootstrap_hash=$(printf '%s\n' "$bootstrap_content" | sha256sum | awk '{print $1}')
for ((attempt = 1; attempt <= 60; attempt++)); do
  received=$(docker exec dfr-node-b dfr-ctl --remote-json hash -- ci-bootstrap.txt 2>/dev/null | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])' 2>/dev/null || true)
  [[ "$received" == "$bootstrap_hash" ]] && break
  sleep 0.25
done
if [[ "${received:-}" != "$bootstrap_hash" ]]; then
  echo "Node B did not receive the file that existed before its first start" >&2
  exit 1
fi

start_daemon dfr-node-c
for container in "${CONTAINERS[@]}"; do
  start_web "$container"
done

for container in "${CONTAINERS[@]}"; do
  ready=false
  for ((attempt = 1; attempt <= 80; attempt++)); do
    if docker exec "$container" dfr-ctl --check --config /etc/dfr/dfr.conf >/dev/null 2>&1 \
      && docker exec "$container" curl -fsS http://127.0.0.1:8080/api/status >/dev/null 2>&1; then
      ready=true
      break
    fi
    sleep 0.25
  done
  if [[ "$ready" != true ]]; then
    echo "DFR services did not become ready in $container" >&2
    exit 1
  fi
done

for index in "${!NODES[@]}"; do
  node="${NODES[$index]}"
  peer_one="${NODES[(index + 1) % 3]}"
  peer_two="${NODES[(index + 2) % 3]}"
  echo "Running replication tests from $node"
  docker exec \
    --env HOME=/var/lib/dfr \
    --env DFR_ROOT=/srv/dfr/data \
    --env DFR_KEY=/var/lib/dfr/.ssh/id_ed25519 \
    --env "DFR_PEER_B=dfr@$peer_one" \
    --env "DFR_PEER_C=dfr@$peer_two" \
    --env "DFR_STRESS=$([[ "$index" == 0 ]] && echo 1 || echo 0)" \
    "dfr-$node" bats /opt/dfr-tests/replication.bats
done

python3 "$ROOT/tests/integration/cluster_acceptance.py" "$NETWORK"

echo "Three-node Debian 12 integration suite passed."
