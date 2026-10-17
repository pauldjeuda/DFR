from pathlib import Path

from dfr.config import Config
from dfr.daemon import DFRDaemon
from dfr.state import State


class FakeTransport:
    def __init__(self):
        self.files = {}
        self.pushed = []
        self.deleted = []

    def stat(self, peer_id, rel):
        item = self.files.get((peer_id, rel))
        if item is None:
            return {"exists": False, "tombstone": None}
        return {
            "exists": True,
            "sha256": item["sha256"],
            "mtime_ns": item["mtime_ns"],
            "mode": item["mode"],
        }

    def push_batch(self, peer_id, items):
        self.pushed.append((peer_id, dict(items)))
        for rel, (digest, mtime_ns, _size, mode) in items.items():
            self.files[(peer_id, rel)] = {"sha256": digest, "mtime_ns": mtime_ns, "mode": mode}

    def delete(self, peer_id, rel, mtime_ns, origin_node=None):
        self.deleted.append((peer_id, rel, mtime_ns, origin_node))
        self.files.pop((peer_id, rel), None)
        return {"deleted": True}


def make_daemon(tmp_path: Path, peer_has_file=False):
    root = tmp_path / "data"
    root.mkdir()
    config = Config(
        node_id="node-a",
        root=root,
        peers={"node-b": "dfr@node-b"},
        state_db=tmp_path / "state.db",
        log_file=tmp_path / "dfr.log",
        socket=tmp_path / "dfr.sock",
        trash_dir=root / ".dfr" / "trash",
        conflicts_dir=root / ".dfr" / "conflicts",
    )
    daemon = object.__new__(DFRDaemon)
    daemon.config = config
    daemon.state = State(config.state_db)
    daemon.transport = FakeTransport()
    daemon.queued_pushes = {}
    daemon.confirmed_peers = {}
    daemon.pending = {}
    daemon.retries = {}
    daemon.initial_reconcile = True
    daemon.last_sync = 0
    daemon.log = lambda *args, **kwargs: None
    return daemon


def test_sync_push_confirms_shared_baseline(tmp_path):
    daemon = make_daemon(tmp_path)
    path = daemon.config.root / "report.txt"
    path.write_text("new local file", encoding="utf-8")
    digest = State.digest(path)

    try:
        daemon._sync_path("report.txt", force_hash=True)
        assert daemon.state.file("report.txt")["synced_sha256"] is None

        daemon.flush_transfers()

        row = daemon.state.file("report.txt")
        assert row["synced_sha256"] == digest
        assert daemon.transport.pushed[0][0] == "node-b"
        assert daemon.transport.files[("node-b", "report.txt")]["sha256"] == digest
    finally:
        daemon.state.close()


def test_existing_equal_remote_file_sets_baseline_after_index_insert(tmp_path):
    daemon = make_daemon(tmp_path)
    path = daemon.config.root / "report.txt"
    path.write_text("already on peer", encoding="utf-8")
    stat = path.stat()
    digest = State.digest(path)
    daemon.transport.files[("node-b", "report.txt")] = {
        "sha256": digest,
        "mtime_ns": stat.st_mtime_ns,
        "mode": stat.st_mode & 0o7777,
    }

    try:
        daemon._sync_path("report.txt", force_hash=True)
        row = daemon.state.file("report.txt")
        assert row["sha256"] == digest
        assert row["synced_sha256"] == digest
        assert "report.txt" not in daemon.confirmed_peers
        assert daemon.transport.pushed == []
    finally:
        daemon.state.close()


def test_restore_conflict_reads_id_from_control_arguments(tmp_path):
    daemon = make_daemon(tmp_path)
    cursor = daemon.state.db.execute(
        """INSERT INTO conflicts(ts,path,winner,loser,winner_mtime,loser_mtime,saved_path)
           VALUES(?,?,?,?,?,?,?)""",
        (1, "report.txt", "node-a", "node-b", 20, 10,
         "remote:node-b:.dfr/conflicts/saved/report.txt"),
    )
    daemon.state.db.commit()
    conflict_id = cursor.lastrowid
    ssh_calls = []
    daemon.transport.ssh = lambda peer, args: ssh_calls.append((peer, args))

    try:
        result = daemon.control({"command": "restore-conflict", "args": {"id": conflict_id}})
        assert result == {"restored": True}
        assert ssh_calls == [
            ("node-b", ["restore-conflict", "report.txt", ".dfr/conflicts/saved/report.txt"])
        ]
    finally:
        daemon.state.close()
