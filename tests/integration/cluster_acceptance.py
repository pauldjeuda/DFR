"""Cluster-level acceptance tests for the three Debian containers."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time

NETWORK = sys.argv[1] if len(sys.argv) > 1 else None
NODES = ("node-a", "node-b", "node-c")
CONTAINER = {node: f"dfr-{node}" for node in NODES}


def docker(node: str, *args: str, input: bytes | None = None, check: bool = True):
    return subprocess.run(
        ["docker", "exec", "-i", CONTAINER[node], *args],
        input=input,
        capture_output=True,
        check=check,
        timeout=30,
    )


def announce(message: str):
    print(message, flush=True)


def put(node: str, relative: str, content: bytes, mtime_ns: int | None = None):
    script = (
        "import os,pathlib,sys; "
        "p=pathlib.Path('/srv/dfr/data')/sys.argv[1]; "
        "p.parent.mkdir(parents=True,exist_ok=True); "
        "p.write_bytes(sys.stdin.buffer.read()); "
        "t=int(sys.argv[2]); os.utime(p,ns=(t,t))"
    )
    stamp = mtime_ns if mtime_ns is not None else time.time_ns()
    docker(node, "python3", "-c", script, relative, str(stamp), input=content)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def local_hash(node: str, relative: str) -> str | None:
    result = docker(
        node,
        "dfr-ctl",
        "--remote-json",
        "hash",
        "--",
        relative,
        check=False,
    )
    if result.returncode:
        return None
    return json.loads(result.stdout)["sha256"]


def wait_hash(relative: str, expected: str, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(local_hash(node, relative) == expected for node in NODES):
            return
        time.sleep(0.25)
    found = {node: local_hash(node, relative) for node in NODES}
    raise AssertionError(f"{relative} failed to converge: {found}; expected {expected}")


def pause_cluster():
    for node in NODES:
        docker(node, "dfr-ctl", "pause")


def resume_cluster():
    for node in NODES:
        docker(node, "dfr-ctl", "resume")


def conflict_rows(node: str, relative: str):
    script = (
        "import json,sqlite3,sys; "
        "db=sqlite3.connect('/var/lib/dfr/state.db'); "
        "db.row_factory=sqlite3.Row; "
        "rows=db.execute('select winner,loser,saved_path from conflicts where path=?', "
        "(sys.argv[1],)).fetchall(); print(json.dumps([dict(r) for r in rows]))"
    )
    result = docker(node, "python3", "-c", script, relative)
    return json.loads(result.stdout)


def check_conflict_archive(rows_by_node):
    for node, rows in rows_by_node.items():
        for row in rows:
            owner = node
            saved = row["saved_path"]
            if saved.startswith("remote:"):
                _, peer, relative = saved.split(":", 2)
                owner = peer
                saved = relative
            result = docker(
                owner,
                "test",
                "-e",
                f"/srv/dfr/data/{saved}",
                check=False,
            )
            if result.returncode == 0:
                return
    raise AssertionError("conflict recorded without a preserved loser version")


def test_multi_master_and_offline_reconciliation():
    announce("IT-08/13: write from B and catch up C after network recovery")
    relative = "ci-acceptance/offline-rejoin.txt"
    content = b"written from node B while C is disconnected\n"
    subprocess.run(
        ["docker", "network", "disconnect", NETWORK, CONTAINER["node-c"]],
        check=True,
        capture_output=True,
    )
    try:
        put("node-b", relative, content)
        expected = digest(content)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if local_hash("node-a", relative) == expected:
                break
            time.sleep(0.25)
        else:
            raise AssertionError("node A did not receive a file created on node B")
    finally:
        subprocess.run(
            ["docker", "network", "connect", "--alias", "node-c", NETWORK, CONTAINER["node-c"]],
            check=True,
            capture_output=True,
        )
    wait_hash(relative, expected, timeout=45)


def test_last_writer_wins_and_loser_preserved():
    announce("IT-09/10: LWW, deterministic timestamp ties and conflict archive")
    relative = "ci-acceptance/conflict-lww.txt"
    old_stamp = time.time_ns() - 10_000_000_000
    newer = b"node B is the later writer\n"
    older = b"node A is the earlier writer\n"
    pause_cluster()
    try:
        put("node-a", relative, older, old_stamp)
        put("node-b", relative, newer, old_stamp + 1_000_000_000)
    finally:
        resume_cluster()
    wait_hash(relative, digest(newer))
    rows = {}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        rows = {node: conflict_rows(node, relative) for node in NODES}
        if any(rows.values()):
            break
        time.sleep(0.25)
    if not any(rows.values()):
        raise AssertionError("no conflict was recorded for concurrent versions")
    if not any(row["winner"] == "node-b" and row["loser"] == "node-a"
               for records in rows.values() for row in records):
        raise AssertionError(f"unexpected LWW conflict records: {rows}")
    check_conflict_archive(rows)

    tied = "ci-acceptance/conflict-tie.txt"
    tied_stamp = time.time_ns() - 10_000_000_000
    winner = b"node A wins equal mtimes\n"
    loser = b"node B loses equal mtimes\n"
    pause_cluster()
    try:
        put("node-a", tied, winner, tied_stamp)
        put("node-b", tied, loser, tied_stamp)
    finally:
        resume_cluster()
    wait_hash(tied, digest(winner))
    rows = {node: conflict_rows(node, tied) for node in NODES}
    if not any(row["winner"] == "node-a" and row["loser"] == "node-b"
               for records in rows.values() for row in records):
        raise AssertionError(f"node-id tie-break was not recorded: {rows}")
    check_conflict_archive(rows)


def test_stable_mesh_does_not_echo():
    announce("IT-12: stable files stop generating replication traffic")
    time.sleep(4)

    def count_pushes(node: str):
        script = (
            "import sqlite3; db=sqlite3.connect('/var/lib/dfr/state.db'); "
            "print(db.execute(\"select count(*) from operations where event in "
            "('sync_push','apply_delete','rename')\").fetchone()[0])"
        )
        return int(docker(node, "python3", "-c", script).stdout)

    before = {node: count_pushes(node) for node in NODES}
    time.sleep(5)
    after = {node: count_pushes(node) for node in NODES}
    if before != after:
        raise AssertionError(f"replication continued without changes: {before} -> {after}")


if __name__ == "__main__":
    if not NETWORK:
        raise SystemExit("usage: cluster_acceptance.py DOCKER_NETWORK")
    test_multi_master_and_offline_reconciliation()
    test_last_writer_wins_and_loser_preserved()
    test_stable_mesh_does_not_echo()
    print("Cluster acceptance tests passed.")
