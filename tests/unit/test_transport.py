from pathlib import Path
from types import SimpleNamespace

from dfr.config import Config
from dfr.state import State
from dfr.transport import Transport


def make_transport(tmp_path: Path, monkeypatch, transfer_stats: bytes):
    root = tmp_path / "data"
    root.mkdir()
    source = root / "report.txt"
    source.write_text("replicated", encoding="utf-8")
    config = Config(
        node_id="node-a",
        root=root,
        peers={"node-b": "dfr@node-b"},
        ssh_key=tmp_path / "id_ed25519",
    )
    state = State(tmp_path / "state.db")
    digest = State.digest(source)
    calls = []
    logs = []

    def fake_ssh(peer, command):
        calls.append((peer, command))
        if command[0] == "hash":
            return {"sha256": digest}
        return {"ok": True}

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b"", stderr=transfer_stats)

    monkeypatch.setattr("dfr.transport.subprocess.run", fake_run)
    transport = Transport(config, state, lambda *args: logs.append(args))
    transport.ssh = fake_ssh
    stat = source.stat()
    item = {"report.txt": (digest, stat.st_mtime_ns, stat.st_size, stat.st_mode & 0o7777)}
    return transport, state, source, item, calls, logs


def test_push_batch_logs_actual_rsync_bytes_and_verifies_hash(tmp_path, monkeypatch):
    transport, state, source, items, calls, logs = make_transport(
        tmp_path, monkeypatch, b"Total bytes sent: 1,234\nTotal bytes received: 567\n"
    )
    try:
        transport.push_batch("node-b", items)
        assert transport.last_transfer_bytes == 1234
        assert any(command[0] == "begin-apply" for _, command in calls)
        assert any(command[0] == "hash" for _, command in calls)
        batch = next(entry for entry in logs if entry[1] == "sync_batch")
        assert batch[6] == 1234
        assert batch[7] == "files=1"
    finally:
        state.close()


def test_push_batch_fails_if_rsync_does_not_report_transfer_stats(tmp_path, monkeypatch):
    transport, state, _source, items, _calls, _logs = make_transport(
        tmp_path, monkeypatch, b"rsync completed without stats\n"
    )
    try:
        try:
            transport.push_batch("node-b", items)
        except RuntimeError as exc:
            assert "statistiques" in str(exc)
        else:
            raise AssertionError("missing rsync transfer statistics should fail the batch")
    finally:
        state.close()


def test_peer_status_checks_remote_shared_root_over_ssh(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    config = Config(node_id="node-a", root=root, peers={"node-b": "dfr@node-b"}, ssh_key=tmp_path / "id_ed25519")
    state = State(tmp_path / "state.db")
    calls = []
    transport = Transport(config, state, lambda *args: None)

    def fake_ssh(peer, command, timeout=30):
        calls.append((peer, command, timeout))
        return {"exists": True, "type": "directory", "node": "node-b", "server_time_ns": 1}

    monkeypatch.setattr(transport, "ssh", fake_ssh)
    try:
        result = transport.peer_status("node-b")
        assert result["node"] == "node-b"
        assert calls == [("node-b", ["stat", "."], 6)]
    finally:
        state.close()
