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
