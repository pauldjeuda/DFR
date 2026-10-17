#!/usr/bin/env bats

setup() {
  : "${DFR_ROOT:?Définir DFR_ROOT sur la racine locale surveillée}"
  : "${DFR_KEY:?Définir DFR_KEY sur la clé privée DFR}"
  : "${DFR_PEER_B:?Définir DFR_PEER_B, par exemple dfr@node-b}"
  : "${DFR_PEER_C:?Définir DFR_PEER_C, par exemple dfr@node-c}"
  REL="dfr-it-${BATS_TEST_NUMBER}.txt"
}

peer_cmd() {
  local peer="$1"; shift
  ssh -i "$DFR_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes "$peer" dfr-ctl --remote-json "$@"
}

wait_remote() {
  local peer="$1" path="$2" expected="$3" i
  for i in $(seq 1 25); do
    local result
    result=$(peer_cmd "$peer" stat -- "$path" 2>/dev/null || true)
    if [[ "$expected" == present && "$result" == *'"exists": true'* ]]; then return 0; fi
    if [[ "$expected" == absent && "$result" == *'"exists": false'* ]]; then return 0; fi
    sleep 0.2
  done
  return 1
}

wait_remote_hash() {
  local peer="$1" path="$2" expected="$3" attempts="${4:-25}" i actual
  for i in $(seq 1 "$attempts"); do
    actual=$(peer_cmd "$peer" hash -- "$path" 2>/dev/null | \
      python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])' 2>/dev/null || true)
    if [[ "$actual" == "$expected" ]]; then return 0; fi
    sleep 0.2
  done
  return 1
}

wait_remote_count() {
  local peer="$1" prefix="$2" expected="$3" i count
  for i in $(seq 1 240); do
    count=$(peer_cmd "$peer" list 2>/dev/null | \
      python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(sys.stdin); print(sum(x["path"].startswith(p) for x in d["files"]))' \
      "$prefix" 2>/dev/null || true)
    if [[ "$count" == "$expected" ]]; then return 0; fi
    sleep 0.5
  done
  return 1
}

teardown() {
  rm -rf "$DFR_ROOT/$REL" "$DFR_ROOT/${REL}.burst" \
    "$DFR_ROOT/${REL}.dir" "$DFR_ROOT/${REL}.moved" \
    "$DFR_ROOT/${REL}.renamed" "$DFR_ROOT/${REL}.large"
}

@test "IT-01 fichier créé sur A arrive sur B et C" {
  local started_ms=$(( $(date +%s%3N) ))
  printf 'DFR create %s\n' "$REL" > "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  wait_remote "$DFR_PEER_C" "$REL" present
  local elapsed_ms=$(( $(date +%s%3N) - started_ms ))
  [ "$elapsed_ms" -lt 5000 ]
}

@test "IT-02 contenu modifié est identique sur les pairs" {
  printf 'DFR initial %s\n' "$REL" > "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  wait_remote "$DFR_PEER_C" "$REL" present
  printf 'DFR changed %s\n' "$REL" > "$DFR_ROOT/$REL"
  local expected
  expected=$(sha256sum "$DFR_ROOT/$REL" | awk '{print $1}')
  for peer in "$DFR_PEER_B" "$DFR_PEER_C"; do
    wait_remote_hash "$peer" "$REL" "$expected"
  done
}

@test "IT-03 suppression devient un tombstone et disparaît des pairs" {
  printf 'DFR delete %s\n' "$REL" > "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  rm "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" absent
  wait_remote "$DFR_PEER_C" "$REL" absent
  peer_cmd "$DFR_PEER_B" stat -- "$REL" | python3 -c \
    'import json,sys; data=json.load(sys.stdin); assert data["tombstone"] is not None'
  [ -n "$(find "$DFR_ROOT/.dfr/trash" -type f -name "*$REL" -print -quit 2>/dev/null)" ]
}

@test "IT-04 renommage conserve le contenu sur les pairs" {
  local new="${REL}.renamed"
  printf 'DFR rename %s\n' "$REL" > "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  mv "$DFR_ROOT/$REL" "$DFR_ROOT/$new"
  wait_remote "$DFR_PEER_B" "$new" present
  wait_remote "$DFR_PEER_C" "$new" present
  wait_remote "$DFR_PEER_B" "$REL" absent
  wait_remote "$DFR_PEER_C" "$REL" absent
  rm -f "$DFR_ROOT/$new"
}

@test "IT-05 renommage de répertoire conserve les descendants" {
  local old_dir="${REL}.dir" new_dir="${REL}.moved"
  mkdir -p "$DFR_ROOT/$old_dir/sub"
  printf 'nested %s\n' "$REL" > "$DFR_ROOT/$old_dir/sub/file.txt"
  wait_remote "$DFR_PEER_B" "$old_dir/sub/file.txt" present
  mv "$DFR_ROOT/$old_dir" "$DFR_ROOT/$new_dir"
  wait_remote "$DFR_PEER_B" "$new_dir/sub/file.txt" present
  wait_remote "$DFR_PEER_C" "$new_dir/sub/file.txt" present
  wait_remote "$DFR_PEER_B" "$old_dir/sub/file.txt" absent
  wait_remote "$DFR_PEER_C" "$old_dir/sub/file.txt" absent
  rm -rf "$DFR_ROOT/$new_dir"
}

@test "IT-06 rafale de 1000 fichiers sans perte" {
  [[ "${DFR_STRESS:-0}" == 1 ]] || skip "rafale mesurée une seule fois par exécution CI"
  local directory="${REL}.burst"
  mkdir "$DFR_ROOT/$directory"
  for index in $(seq -w 1 1000); do
    printf 'burst %s\n' "$index" > "$DFR_ROOT/$directory/$index.txt"
  done
  wait_remote_count "$DFR_PEER_B" "$directory/" 1000
  wait_remote_count "$DFR_PEER_C" "$directory/" 1000
  rm -rf "$DFR_ROOT/$directory"
}

@test "IT-07 modification de 1 MiB dans un fichier de 1 GiB utilise le delta rsync" {
  [[ "${DFR_STRESS:-0}" == 1 ]] || skip "gros transfert mesuré une seule fois par exécution CI"
  local large="${REL}.large" expected before_id sent
  truncate -s 1073741824 "$DFR_ROOT/$large"
  expected=$(dfr-ctl --config /etc/dfr/dfr.conf --remote-json hash -- "$large" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')
  wait_remote_hash "$DFR_PEER_B" "$large" "$expected" 1800
  wait_remote_hash "$DFR_PEER_C" "$large" "$expected" 1800

  before_id=$(python3 -c 'import sqlite3; db=sqlite3.connect("/var/lib/dfr/state.db"); print(db.execute("select coalesce(max(id),0) from operations").fetchone()[0])')
  dd if=/dev/urandom of="$DFR_ROOT/$large" bs=1M count=1 seek=512 conv=notrunc status=none
  expected=$(dfr-ctl --config /etc/dfr/dfr.conf --remote-json hash -- "$large" | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')
  wait_remote_hash "$DFR_PEER_B" "$large" "$expected"
  wait_remote_hash "$DFR_PEER_C" "$large" "$expected"
  sent=$(python3 -c 'import sqlite3,sys; db=sqlite3.connect("/var/lib/dfr/state.db"); print(db.execute("select coalesce(sum(bytes),0) from operations where event=\"sync_batch\" and id> ?",(int(sys.argv[1]),)).fetchone()[0])' "$before_id")
  [ "$sent" -gt 0 ]
  [ "$sent" -lt $((20 * 1024 * 1024)) ]
}

@test "IT-20 un fichier ouvert n'est répliqué qu'après sa fermeture" {
  exec 3>"$DFR_ROOT/$REL"
  printf 'partial' >&3
  sleep 1
  run peer_cmd "$DFR_PEER_B" stat -- "$REL"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"exists": false'* ]]
  printf ' complete\n' >&3
  exec 3>&-
  wait_remote "$DFR_PEER_B" "$REL" present
  wait_remote "$DFR_PEER_C" "$REL" present
}

@test "IT-21 une cible symbolique reste un lien et garde sa cible texte" {
  local target="../outside-${REL}"
  ln -s "$target" "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  wait_remote "$DFR_PEER_C" "$REL" present
  local expected actual
  expected=$(printf '%s' "$target" | sha256sum | awk '{print $1}')
  for peer in "$DFR_PEER_B" "$DFR_PEER_C"; do
    actual=$(peer_cmd "$peer" hash -- "$REL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')
    [ "$expected" = "$actual" ]
  done
  rm "$DFR_ROOT/$REL"
}

@test "IT-17 un chemin parent est refusé par le pair SSH" {
  run peer_cmd "$DFR_PEER_B" stat -- ../etc/passwd
  [ "$status" -ne 0 ]
}

@test "IT-23 tableau de bord: état, pause, reprise et export JSON/CSV" {
  local api=http://127.0.0.1:8080
  curl -fsS "$api/api/status" | python3 -c \
    'import json,sys; data=json.load(sys.stdin); assert data["node"]'
  curl -fsS -X POST "$api/api/control/pause" | python3 -c \
    'import json,sys; assert json.load(sys.stdin)["paused"] is True'
  [ "$(dfr-ctl --config /etc/dfr/dfr.conf status | python3 -c 'import json,sys; print(json.load(sys.stdin)["paused"])')" = True ]
  curl -fsS -X POST "$api/api/control/resume" | python3 -c \
    'import json,sys; assert json.load(sys.stdin)["paused"] is False'
  curl -fsS "$api/api/logs/export?format=json" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin),list)'
  curl -fsS "$api/api/logs/export?format=csv" | grep -q 'ts,level,node,event'
}
