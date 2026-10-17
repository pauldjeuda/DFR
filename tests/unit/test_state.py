import os

from dfr.state import State


def test_unsynced_record_preserves_common_baseline(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        state.record_file("report.txt", 4, 10, "common", synced=True)
        state.record_file("report.txt", 5, 20, "local-change", synced=False)

        row = state.file("report.txt")
        assert row["sha256"] == "local-change"
        assert row["synced_sha256"] == "common"
    finally:
        state.close()


def test_tombstone_merge_is_lww_and_does_not_refresh_equal_entry(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        state.add_tombstone("gone.txt", 20, "node-b")
        created = state.db.execute(
            "SELECT created FROM tombstones WHERE path=?", ("gone.txt",)
        ).fetchone()[0]

        state.add_tombstone("gone.txt", 10, "node-c")
        state.add_tombstone("gone.txt", 20, "node-b")
        row = state.db.execute(
            "SELECT mtime_ns,node,created FROM tombstones WHERE path=?", ("gone.txt",)
        ).fetchone()

        assert row["mtime_ns"] == 20
        assert row["node"] == "node-b"
        assert row["created"] == created

        state.add_tombstone("gone.txt", 20, "node-a")
        row = state.db.execute(
            "SELECT mtime_ns,node FROM tombstones WHERE path=?", ("gone.txt",)
        ).fetchone()
        assert row["mtime_ns"] == 20
        assert row["node"] == "node-a"
    finally:
        state.close()


def test_log_filters_and_pagination(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        state.log("node-a", "info", "sync", path="a.txt", peer="node-b")
        state.log("node-a", "error", "sync_error", path="b.txt", peer="node-c")

        rows = state.logs(limit=1, filters={"level": "error", "peer": "node-c"})
        assert len(rows) == 1
        assert rows[0]["path"] == "b.txt"
        assert rows[0]["event"] == "sync_error"
    finally:
        state.close()


def test_digest_and_fingerprint_cache_respects_force_and_metadata(tmp_path):
    path = tmp_path / "report.txt"
    path.write_text("first", encoding="utf-8")
    state = State(tmp_path / "state.db")
    try:
        digest = State.digest(path)
        stat = path.stat()
        state.record_file("report.txt", stat.st_size, stat.st_mtime_ns, digest)
        assert state.fingerprint(path, "report.txt", 100) == digest

        path.write_text("other", encoding="utf-8")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert state.fingerprint(path, "report.txt", 100) == digest
        assert state.fingerprint(path, "report.txt", 100, force=True) != digest
    finally:
        state.close()


def test_symlink_digest_hashes_link_target_without_following(tmp_path):
    target = tmp_path / "outside.txt"
    target.write_text("secret target", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        import pytest

        pytest.skip(f"symlink unavailable on this host: {exc}")
    assert State.digest(link) != State.digest(target)


def test_record_file_removes_tombstone_and_conflicts_are_listed(tmp_path):
    state = State(tmp_path / "state.db")
    try:
        state.add_tombstone("report.txt", 10, "node-b")
        state.record_file("report.txt", 4, 20, "digest", synced=False, origin="peer")
        assert state.db.execute("SELECT 1 FROM tombstones WHERE path='report.txt'").fetchone() is None
        assert state.file("report.txt")["origin"] == "peer"

        state.db.execute(
            """INSERT INTO conflicts(ts,path,winner,loser,winner_mtime,loser_mtime,saved_path)
               VALUES(?,?,?,?,?,?,?)""",
            (1, "report.txt", "node-a", "node-b", 20, 10, ".dfr/conflicts/one/report.txt"),
        )
        state.db.commit()
        assert state.conflicts()[0]["loser"] == "node-b"
    finally:
        state.close()
