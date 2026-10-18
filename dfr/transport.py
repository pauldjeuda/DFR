"""rsync/SSH transport and remote DFR operations."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time


class Transport:
    def __init__(self, config, state, logger):
        self.config, self.state, self.logger = config, state, logger
        self.last_transfer_bytes = 0

    def ssh(self, peer_id: str, command: list[str], timeout=30):
        target = self.config.peers[peer_id]
        argv = ["ssh", "-i", str(self.config.ssh_key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                "-o", "ControlMaster=auto", "-o", "ControlPersist=60", "-o", "ControlPath=/var/lib/dfr/.ssh/cm-%C",
                "-o", "StrictHostKeyChecking=yes",
                target, shlex.join(["dfr-ctl", "--remote-json", command[0], "--", *command[1:]])]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode: raise RuntimeError(result.stderr.strip() or f"commande distante en échec ({result.returncode})")
        try: return json.loads(result.stdout)
        except json.JSONDecodeError as exc: raise RuntimeError("réponse distante invalide") from exc

    def push(self, peer_id: str, rel: str):
        source = self.config.root / rel
        if not source.exists() and not source.is_symlink(): return
        if source.is_dir() and not source.is_symlink(): return
        self.push_batch(peer_id, {rel: (self.state.fingerprint(source, rel, self.config.max_hash_size_mb),
                                      source.lstat().st_mtime_ns, source.lstat().st_size,
                                      source.lstat().st_mode & 0o7777)})

    def push_batch(self, peer_id: str, items: dict[str, tuple[str, int, int, int]]):
        items = {rel: info for rel, info in items.items()
                 if ((self.config.root / rel).exists() or (self.config.root / rel).is_symlink())
                 and not ((self.config.root / rel).is_dir() and not (self.config.root / rel).is_symlink())}
        if not items: return
        target = self.config.peers[peer_id]
        start = time.monotonic()
        for rel, (digest, mtime_ns, _size, _mode) in items.items():
            self.ssh(peer_id, ["begin-apply", rel, digest, str(mtime_ns)])
        command = ["rsync", "-a", "--stats", "--partial", "--partial-dir=.dfr/tmp", "--relative", "--from0", "--files-from=-",
                   "--exclude=.dfr/", "-e", f"ssh -i {self.config.ssh_key} -o BatchMode=yes -o StrictHostKeyChecking=yes -o ControlMaster=auto -o ControlPersist=60 -o ControlPath=/var/lib/dfr/.ssh/cm-%C",
                   "./", f"{target}:{self.config.root.as_posix()}/"]
        file_list = b"".join(("./" + rel).encode("utf-8", errors="surrogateescape") + b"\0" for rel in items)
        env = {**os.environ, "LC_ALL": "C"}
        result = subprocess.run(command, cwd=self.config.root, input=file_list, capture_output=True, timeout=3600, check=False, env=env)
        if result.returncode: raise RuntimeError(result.stderr.strip() or f"rsync en échec ({result.returncode})")
        output = (result.stdout or b"") + b"\n" + (result.stderr or b"")
        stats = re.search(rb"Total bytes sent:\s*([0-9,]+)", output)
        if not stats: raise RuntimeError("statistiques de transfert rsync introuvables")
        self.last_transfer_bytes = int(stats.group(1).replace(b",", b""))
        elapsed = int((time.monotonic() - start) * 1000)
        self.logger("info", "sync_batch", "", peer_id, "ok", elapsed, self.last_transfer_bytes,
                    f"files={len(items)}")
        for rel, (digest, mtime_ns, size, _mode) in items.items():
            try:
                remote_hash = self.ssh(peer_id, ["hash", rel])
                if remote_hash.get("sha256") != digest: raise RuntimeError("empreinte distante différente après transfert")
                self.ssh(peer_id, ["record", rel, digest, str(mtime_ns), str(size)])
                self.logger("info", "sync_push", rel, peer_id, "ok", elapsed, size)
            except Exception as exc:
                self.logger("error", "sync_verify_error", rel, peer_id, "failed", elapsed, size, str(exc))
                raise
        self.state.db.execute("UPDATE peers SET online=1,last_success=?,last_error='' WHERE id=?", (time.time(), peer_id)); self.state.db.commit()

    def delete(self, peer_id: str, rel: str, mtime_ns: int, origin_node: str | None = None):
        return self.ssh(peer_id, ["apply-delete", rel, str(mtime_ns), origin_node or self.config.node_id])

    def rename(self, peer_id: str, old: str, new: str):
        self.ssh(peer_id, ["apply-rename", old, new, self.config.node_id])

    def stat(self, peer_id: str, rel: str):
        result = self.ssh(peer_id, ["stat", rel])
        self._clock(peer_id, result)
        return result

    def list(self, peer_id: str):
        result = self.ssh(peer_id, ["list"])
        self._clock(peer_id, result)
        return result

    def _clock(self, peer_id: str, result):
        if not result.get("server_time_ns"): return
        delta = (int(result["server_time_ns"]) - time.time_ns()) / 1_000_000_000
        previous = self.state.db.execute("SELECT clock_delta FROM peers WHERE id=?", (peer_id,)).fetchone()
        self.state.db.execute("UPDATE peers SET clock_delta=? WHERE id=?", (delta, peer_id)); self.state.db.commit()
        if abs(delta) > 2 and (not previous or abs(previous[0]) <= 2):
            self.logger("warning", "clock_drift", "", peer_id, "alert", 0, 0, f"écart d'horloge {delta:.2f} s")
