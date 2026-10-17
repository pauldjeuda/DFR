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
  for i in $(seq 1 100); do
    local result
    result=$(peer_cmd "$peer" stat -- "$path" 2>/dev/null || true)
    if [[ "$expected" == present && "$result" == *'"exists": true'* ]]; then return 0; fi
    if [[ "$expected" == absent && "$result" == *'"exists": false'* ]]; then return 0; fi
    sleep 0.2
  done
  return 1
}

teardown() {
  rm -f "$DFR_ROOT/$REL"
}

@test "IT-01 fichier créé sur A arrive sur B et C" {
  printf 'DFR create %s\n' "$REL" > "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  wait_remote "$DFR_PEER_C" "$REL" present
}

@test "IT-02 contenu modifié est identique sur les pairs" {
  printf 'DFR changed %s\n' "$REL" > "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  local expected actual
  expected=$(sha256sum "$DFR_ROOT/$REL" | awk '{print $1}')
  actual=$(peer_cmd "$DFR_PEER_B" hash -- "$REL" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')
  [ "$expected" = "$actual" ]
}

@test "IT-03 suppression devient un tombstone et disparaît des pairs" {
  printf 'DFR delete %s\n' "$REL" > "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  rm "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" absent
}

@test "IT-04 renommage conserve le contenu sur les pairs" {
  local new="${REL}.renamed"
  printf 'DFR rename %s\n' "$REL" > "$DFR_ROOT/$REL"
  wait_remote "$DFR_PEER_B" "$REL" present
  mv "$DFR_ROOT/$REL" "$DFR_ROOT/$new"
  wait_remote "$DFR_PEER_B" "$new" present
  wait_remote "$DFR_PEER_B" "$REL" absent
  rm -f "$DFR_ROOT/$new"
}
