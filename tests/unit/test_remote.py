from pathlib import Path

from dfr.cli import remote
from dfr.config import Config
from dfr.daemon import DFRDaemon
from dfr.state import State


def make_config(tmp_path: Path) -> Config:
    root = tmp_path / "data"
    root.mkdir()
    return Config(
        node_id="node-a",
        root=root,
        state_db=tmp_path / "state.db",
        log_file=tmp_path / "dfr.log",
        socket=tmp_path / "dfr.sock",
        trash_dir=root / ".dfr" / "trash",
        conflicts_dir=root / ".dfr" / "conflicts",
    )


def test_remote_inventory_does_not_claim_unreplicated_changes_are_synced(tmp_path):
    config = make_config(tmp_path)
    path = config.root / "report.txt"
    path.write_text("old", encoding="utf-8")
    with State(config.state_db) as state:
        stat = path.stat()
        state.record_file("report.txt", stat.st_size, stat.st_mtime_ns, "common", synced=True)

    path.write_text("new", encoding="utf-8")
    result = remote(config, "list", [])

    with State(config.state_db) as state:
        row = state.file("report.txt")
        assert result["files"][0]["sha256"] == State.digest(path)
        assert row["sha256"] == State.digest(path)
        assert row["synced_sha256"] == "common"


def test_remote_delete_moves_file_to_trash_and_records_tombstone(tmp_path):
    config = make_config(tmp_path)
    path = config.root / "report.txt"
    path.write_text("old", encoding="utf-8")
    with State(config.state_db) as state:
        stat = path.stat()
        state.record_file("report.txt", stat.st_size, stat.st_mtime_ns, "old-hash", synced=True)
    tombstone_mtime = path.stat().st_mtime_ns + 1

    result = remote(config, "apply-delete", ["report.txt", str(tombstone_mtime), "node-b"])

    assert result["deleted"] is True
    assert not path.exists()
    assert list(config.trash_dir.glob("*report.txt"))
    with State(config.state_db) as state:
        tombstone = state.db.execute(
            "SELECT mtime_ns,node FROM tombstones WHERE path=?", ("report.txt",)
        ).fetchone()
        assert tombstone["node"] == "node-b"
        assert tombstone["mtime_ns"] == tombstone_mtime


def test_remote_delete_keeps_stronger_existing_tombstone(tmp_path):
    config = make_config(tmp_path)
    path = config.root / "report.txt"
    path.write_text("old", encoding="utf-8")
    with State(config.state_db) as state:
        stat = path.stat()
        state.record_file("report.txt", stat.st_size, stat.st_mtime_ns, "old-hash", synced=True)
    tombstone_mtime = path.stat().st_mtime_ns + 10
    with State(config.state_db) as state:
        state.add_tombstone("report.txt", tombstone_mtime, "node-b")

    result = remote(config, "apply-delete", ["report.txt", str(path.stat().st_mtime_ns + 1), "node-c"])

    assert result["deleted"] is True
    assert not path.exists()
    with State(config.state_db) as state:
        tombstone = state.db.execute(
            "SELECT mtime_ns,node FROM tombstones WHERE path=?", ("report.txt",)
        ).fetchone()
        assert tombstone["mtime_ns"] == tombstone_mtime
        assert tombstone["node"] == "node-b"


def test_daemon_applies_and_propagates_remote_tombstone(tmp_path):
    config = make_config(tmp_path)
    config.peers = {"node-b": "dfr@node-b", "node-c": "dfr@node-c"}
    path = config.root / "report.txt"
    path.write_text("old", encoding="utf-8")
    with State(config.state_db) as state:
        stat = path.stat()
        state.record_file("report.txt", stat.st_size, stat.st_mtime_ns, "old-hash", synced=True)
    tombstone_mtime = path.stat().st_mtime_ns + 1

    class FakeTransport:
        def __init__(self):
            self.deleted = []

        def delete(self, peer_id, rel, mtime_ns, origin_node=None):
            self.deleted.append((peer_id, rel, mtime_ns, origin_node))
            return {"deleted": True}

    daemon = object.__new__(DFRDaemon)
    daemon.config = config
    daemon.state = State(config.state_db)
    daemon.transport = FakeTransport()
    daemon.queued_pushes = {}
    daemon.confirmed_peers = {}
    daemon.pending = {}
    daemon.log = lambda *args, **kwargs: None
    try:
        daemon._apply_remote_tombstone(
            "node-b", "report.txt", {"mtime_ns": tombstone_mtime, "node": "node-b"}
        )
        assert not path.exists()
        assert list(config.trash_dir.glob("*report.txt"))
        assert daemon.transport.deleted == [
            ("node-c", "report.txt", tombstone_mtime, "node-b")
        ]
        tombstone = daemon.state.db.execute(
            "SELECT mtime_ns,node FROM tombstones WHERE path=?", ("report.txt",)
        ).fetchone()
        assert tombstone["mtime_ns"] == tombstone_mtime
        assert tombstone["node"] == "node-b"
    finally:
        daemon.state.close()


def test_remote_rename_moves_file_and_index(tmp_path):
    config = make_config(tmp_path)
    old_path = config.root / "old.txt"
    old_path.write_text("content", encoding="utf-8")
    with State(config.state_db) as state:
        stat = old_path.stat()
        state.record_file("old.txt", stat.st_size, stat.st_mtime_ns, "hash", synced=True)

    result = remote(config, "apply-rename", ["old.txt", "new.txt", "node-b"])

    assert result["renamed"] is True
    assert not old_path.exists()
    assert (config.root / "new.txt").read_text(encoding="utf-8") == "content"
    with State(config.state_db) as state:
        assert state.file("old.txt") is None
        assert state.file("new.txt")["sha256"] == "hash"


def test_remote_conflict_restore_accepts_internal_archive_path(tmp_path):
    config = make_config(tmp_path)
    destination = config.root / "report.txt"
    destination.write_text("winner", encoding="utf-8")
    archive = config.conflicts_dir / "saved" / "report.txt"
    archive.parent.mkdir(parents=True)
    archive.write_text("loser", encoding="utf-8")

    result = remote(config, "restore-conflict", ["report.txt", ".dfr/conflicts/saved/report.txt"])

    assert result["restored"] is True
    assert destination.read_text(encoding="utf-8") == "loser"
