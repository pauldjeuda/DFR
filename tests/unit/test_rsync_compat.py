"""Regression tests for rsync's SSH command protocol."""
from __future__ import annotations

import os
import runpy
from pathlib import Path
from types import SimpleNamespace

from dfr.config import Config
from dfr.daemon import DFRDaemon
from dfr.state import State
from dfr.transport import Transport


def test_push_batch_rsync_command_omits_protect_args(tmp_path, monkeypatch):
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
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=b"Total bytes sent: 10\n",
            stderr=b"",
        )

    transport = Transport(config, state, lambda *_args: None)
    transport.ssh = lambda _peer, command: {"sha256": digest} if command[0] == "hash" else {"ok": True}
    monkeypatch.setattr("dfr.transport.subprocess.run", fake_run)
    stat = source.stat()
    try:
        transport.push_batch(
            "node-b",
            {"report.txt": (digest, stat.st_mtime_ns, stat.st_size, stat.st_mode & 0o7777)},
        )
        assert len(commands) == 1
        assert "--protect-args" not in commands[0]
    finally:
        state.close()


def test_pull_rsync_command_omits_protect_args(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    config = Config(
        node_id="node-a",
        root=root,
        peers={"node-b": "dfr@node-b"},
        state_db=tmp_path / "state.db",
        ssh_key=tmp_path / "id_ed25519",
    )
    state = State(config.state_db)
    daemon = object.__new__(DFRDaemon)
    daemon.config = config
    daemon.state = state
    daemon.queued_pushes = {}
    daemon.confirmed_peers = {}
    daemon._snapshot = lambda *_args: None
    daemon._confirm_peer = lambda *_args: None
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"pulled from peer")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    try:
        daemon._pull("node-b", "report.txt")
        assert len(commands) == 1
        assert "--protect-args" not in commands[0]
        assert (root / "report.txt").read_bytes() == b"pulled from peer"
    finally:
        state.close()


def test_ssh_gate_accepts_rsync_server_command_with_partial_dir(tmp_path, monkeypatch):
    class ExecCalled(BaseException):
        pass

    executed = []
    monkeypatch.setattr(
        Config,
        "load",
        classmethod(lambda _cls, _filename: SimpleNamespace(root=Path("/srv/dfr/data"))),
    )
    monkeypatch.setenv(
        "SSH_ORIGINAL_COMMAND",
        "rsync --server -logDtpRe.LsfxCIvu --partial-dir .dfr/tmp . /srv/dfr/data/",
    )

    def fake_execv(path, argv):
        executed.append((path, argv))
        raise ExecCalled

    monkeypatch.setattr(os, "execv", fake_execv)
    gate = Path(__file__).parents[2] / "scripts" / "ssh-gate"
    try:
        runpy.run_path(str(gate), run_name="__main__")
    except ExecCalled:
        pass
    else:
        raise AssertionError("ssh-gate did not delegate the permitted rsync server command")

    assert executed == [
        (
            "/usr/bin/rsync",
            [
                "rsync",
                "--server",
                "-logDtpRe.LsfxCIvu",
                "--partial-dir",
                ".dfr/tmp",
                ".",
                "/srv/dfr/data/",
            ],
        )
    ]
