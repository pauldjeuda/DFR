"""DFR background service: watch, queue, reconcile, replicate and control socket."""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import signal
import socketserver
import stat as statmod
import threading
import time
from logging.handlers import WatchedFileHandler
from pathlib import Path

from .config import Config
from .state import State
from .transport import Transport
from .watcher import Watcher


class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(), "node": getattr(record, "node", ""),
            "event": getattr(record, "event", record.getMessage()), "path": getattr(record, "path", ""),
            "peer": getattr(record, "peer", ""), "result": getattr(record, "result", ""),
            "duration_ms": getattr(record, "duration_ms", 0), "bytes": getattr(record, "size", 0),
            "detail": record.getMessage()}, ensure_ascii=False)


def make_logger(config):
    logger = logging.getLogger("dfr")
    logger.setLevel(getattr(logging, config.log_level.upper()))
    formatter = JSONFormatter()
    stream = logging.StreamHandler(); stream.setFormatter(formatter); logger.addHandler(stream)
    try:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = WatchedFileHandler(config.log_file, encoding="utf-8")
        file_handler.setFormatter(formatter); logger.addHandler(file_handler)
    except OSError:
        logger.warning("journal fichier indisponible ; journald reste actif")
    return logger


_UnixStreamServer = getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)


class ControlServer(socketserver.ThreadingMixIn, _UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path, app): self.app = app; super().__init__(str(path), ControlHandler)


class ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            try:
                request = json.loads(raw)
                result = self.server.app.control(request)
                self.wfile.write((json.dumps({"ok": True, "result": result}) + "\n").encode())
            except Exception as exc:
                self.wfile.write((json.dumps({"ok": False, "error": str(exc)}) + "\n").encode())


class DFRDaemon:
    def __init__(self, config: Config, config_path="/etc/dfr/dfr.conf"):
        self.config = config
        self.config_path = config_path
        self.config.root.mkdir(parents=True, exist_ok=True)
        self.state = State(config.state_db)
        self.logger = make_logger(config)
        self.transport = Transport(config, self.state, self.log)
        self.pending = {}
        self.queued_pushes = {}
        self.confirmed_peers = {}
        self.last_sync = 0.0
        self.running = True
        self.paused = False
        self.started = time.time()
        self.watcher = Watcher(config.root, config.excludes)
        for peer_id, target in config.peers.items():
            self.state.db.execute("INSERT OR IGNORE INTO peers(id,target) VALUES(?,?)", (peer_id, target))
            self.state.db.execute("UPDATE peers SET target=? WHERE id=?", (target, peer_id))
        self.state.db.commit()
        self.move_from = {}
        self.retries = {}
        self.initial_reconcile = True
        self.reload_requested = False
        self.reconcile_requested = False

    def log(self, level, event, path="", peer="", result="", duration_ms=0, size=0, detail=""):
        self.state.log(self.config.node_id, level, event, path, peer, result, duration_ms, size, detail)
        method = getattr(self.logger, level if level in ("debug", "info", "warning", "error") else "info")
        method(detail or event, extra={"node": self.config.node_id, "event": event, "path": path,
            "peer": peer, "result": result, "duration_ms": duration_ms, "size": size})

    def safe_path(self, rel: str, allow_internal: bool = False) -> Path:
        if not rel or "\x00" in rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ValueError("chemin relatif invalide")
        if not allow_internal and ".dfr" in Path(rel).parts:
            raise ValueError("chemin réservé au service DFR")
        target = self.config.root / rel
        resolved_parent = target.parent.resolve()
        if resolved_parent != self.config.root.resolve() and self.config.root.resolve() not in resolved_parent.parents:
            raise ValueError("chemin sortant de la racine")
        return target

    def control(self, request):
        action = request.get("command")
        if action == "status":
            return {"node": self.config.node_id, "running": self.running, "paused": self.paused,
                "queue": len(self.pending) + sum(len(batch) for batch in self.queued_pushes.values()),
                "uptime_s": int(time.time()-self.started),
                "last_success": self.last_sync, "root": str(self.config.root)}
        if action == "peers": return self.state.peers_status()
        if action == "logs":
            args = dict(request.get("args", {}))
            limit = args.pop("limit", 200); offset = args.pop("offset", 0)
            return self.state.logs(limit=limit, offset=offset, filters=args)
        if action == "conflicts": return self.state.conflicts()
        if action == "audit":
            self.log("info", "web_access", path=request.get("path", ""), result=str(request.get("status", "")),
                     detail="requête interface web")
            return {"logged": True}
        if action == "pause": self.paused = True; self.log("warning", "pause", detail="Réplication mise en pause"); return {"paused": True}
        if action == "resume": self.paused = False; self.log("info", "resume"); return {"paused": False}
        if action == "resync": self.reconcile_requested = True; return {"queued": True}
        if action == "reload": self.reload_requested = True; return {"queued": True}
        if action == "restore-conflict":
            args = request.get("args") or {}
            conflict_id = int(args.get("id", request.get("id", 0)))
            row = self.state.db.execute("SELECT * FROM conflicts WHERE id=?", (conflict_id,)).fetchone()
            if not row: raise ValueError("conflit introuvable")
            saved_path = row["saved_path"]
            if saved_path.startswith("remote:"):
                _, peer_id, archived_rel = saved_path.split(":", 2)
                self.transport.ssh(peer_id, ["restore-conflict", row["path"], archived_rel])
            else:
                archived = self.safe_path(saved_path, allow_internal=True)
                dest = self.safe_path(row["path"])
                if not archived.exists() and not archived.is_symlink(): raise FileNotFoundError("version conservée introuvable")
                dest.parent.mkdir(parents=True, exist_ok=True)
                temp = self.config.root / ".dfr" / "tmp" / row["path"]
                temp.parent.mkdir(parents=True, exist_ok=True)
                if temp.exists() or temp.is_symlink(): temp.unlink()
                if archived.is_symlink(): temp.symlink_to(archived.readlink())
                else: shutil.copy2(archived, temp)
                temp.replace(dest)
                os.utime(dest, None, follow_symlinks=not dest.is_symlink())
                self.pending[row["path"]] = time.monotonic()
            self.log("warning", "conflict_restore", row["path"], result="requested")
            return {"restored": True}
        raise ValueError("commande de contrôle inconnue")

    def reload_config(self):
        updated = Config.load(self.config_path)
        restart_only = ("node_id", "root", "state_db", "socket", "bind")
        changed = [name for name in restart_only if getattr(updated, name) != getattr(self.config, name)]
        if changed:
            raise ValueError(f"modification de {', '.join(changed)} nécessite un redémarrage")
        self.flush_transfers()
        updated.root.mkdir(parents=True, exist_ok=True)
        old_watcher = self.watcher
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            handler.close()
        self.config = updated
        self.logger = make_logger(updated)
        self.transport = Transport(updated, self.state, self.log)
        self.watcher = Watcher(updated.root, updated.excludes)
        self.confirmed_peers.clear()
        self.reconcile_requested = True
        old_watcher.close()
        peer_ids = tuple(updated.peers)
        if peer_ids:
            placeholders = ",".join("?" for _ in peer_ids)
            self.state.db.execute(f"DELETE FROM peers WHERE id NOT IN ({placeholders})", peer_ids)
        else:
            self.state.db.execute("DELETE FROM peers")
        for peer_id, target in updated.peers.items():
            self.state.db.execute("INSERT OR IGNORE INTO peers(id,target) VALUES(?,?)", (peer_id, target))
            self.state.db.execute("UPDATE peers SET target=? WHERE id=?", (target, peer_id))
        self.state.db.commit()
        self.log("info", "reload", result="ok")

    def _copy_conflict(self, source: Path, rel: str, node: str):
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f"-{time.time_ns() % 1_000_000_000:09d}"
        destination = self.config.conflicts_dir / f"{stamp}-{node}" / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
        return destination

    def _archive_and_trash(self, rel: str):
        old = self.state.file(rel)
        if not old: return
        backup = self.config.root / ".dfr" / "versions" / rel
        if backup.exists() or backup.is_symlink():
            target = self.config.trash_dir / f"{time.time_ns()}-{rel}"
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(backup, target, follow_symlinks=False)
            backup.unlink()
        self.state.add_tombstone(rel, int(time.time_ns()), self.config.node_id)

    def _snapshot(self, source: Path, rel: str):
        target = self.config.root / ".dfr" / "versions" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink(): target.unlink()
        if source.is_symlink():
            target.symlink_to(source.readlink())
        else:
            shutil.copy2(source, target)

    def _sync_path(self, rel: str, force_hash: bool = False):
        source = self.safe_path(rel)
        if not source.exists() and not source.is_symlink():
            for batch in self.queued_pushes.values(): batch.pop(rel, None)
            self.confirmed_peers.pop(rel, None)
            descendants = self.state.db.execute(
                "SELECT path FROM files WHERE substr(path,1,?)=? ORDER BY path",
                (len(rel) + 1, rel + "/"),
            ).fetchall()
            for row in descendants:
                self._sync_path(row["path"])
            self._archive_and_trash(rel)
            tomb = self.state.db.execute("SELECT mtime_ns FROM tombstones WHERE path=?", (rel,)).fetchone()
            if not tomb:
                self.state.add_tombstone(rel, time.time_ns(), self.config.node_id)
                tomb = self.state.db.execute("SELECT mtime_ns FROM tombstones WHERE path=?", (rel,)).fetchone()
            for peer_id in self.config.peers:
                try:
                    result = self.transport.delete(peer_id, rel, tomb[0] if tomb else time.time_ns())
                    self.retries.pop((peer_id, rel), None)
                    if result.get("newer"):
                        self._pull(peer_id, rel)
                        self.pending[rel] = time.monotonic()
                        self.log("warning", "delete_conflict", rel, peer_id, "modification retained")
                        return
                except Exception as exc: self._peer_error(peer_id, rel, exc)
            self.log("info", "delete", rel, result="tombstone")
            return
        if source.is_dir() and not source.is_symlink(): return
        if not source.exists() and not source.is_symlink(): return
        mode = source.lstat().st_mode
        if not (statmod.S_ISREG(mode) or statmod.S_ISLNK(mode)):
            self.log("warning", "special_file_ignored", rel, result="ignored")
            return
        previous = self.state.file(rel)
        current_digest = self.state.fingerprint(source, rel, self.config.max_hash_size_mb, force=force_hash)
        if previous and previous["sha256"] != current_digest:
            self.confirmed_peers.pop(rel, None)
        for peer_id in self.config.peers:
            try:
                stat = source.lstat(); digest = current_digest
                old = self.state.file(rel)
                local_changed = old is None or old["synced_sha256"] != digest
                remote = self.transport.stat(peer_id, rel)
                self.retries.pop((peer_id, rel), None)
                remote_hash = remote.get("sha256") if remote.get("exists") else None
                remote_mtime = int(remote.get("mtime_ns", 0))
                remote_tomb = remote.get("tombstone")
                if remote_tomb and int(remote_tomb["mtime_ns"]) >= stat.st_mtime_ns:
                    saved = self._copy_conflict(source, rel, self.config.node_id)
                    self._record_conflict(rel, remote_tomb["node"], self.config.node_id,
                                          int(remote_tomb["mtime_ns"]), stat.st_mtime_ns,
                                          saved.relative_to(self.config.root).as_posix())
                    trash = self.config.trash_dir / f"{time.time_ns()}-{rel}"
                    trash.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(source), str(trash))
                    self.state.add_tombstone(rel, int(remote_tomb["mtime_ns"]), remote_tomb["node"])
                    for batch in self.queued_pushes.values(): batch.pop(rel, None)
                    for other_peer in self.config.peers:
                        if other_peer == peer_id: continue
                        try: self.transport.delete(other_peer, rel, int(remote_tomb["mtime_ns"]), remote_tomb["node"])
                        except Exception as exc: self._peer_error(other_peer, rel, exc)
                    self.log("info", "delete", rel, peer_id, "remote tombstone won")
                    return
                local_mode = statmod.S_IMODE(stat.st_mode)
                if remote_hash == digest and int(remote.get("mode", local_mode)) == local_mode:
                    self._confirm_peer(rel, peer_id, digest)
                    continue
                if remote_hash and old and remote_hash != old["synced_sha256"] and not local_changed and not self.initial_reconcile:
                    self._pull(peer_id, rel)
                    self._confirm_peer(rel, peer_id, self.state.file(rel)["sha256"])
                    continue
                if remote_hash and (old is None or remote_hash != old["synced_sha256"]):
                    local_wins = stat.st_mtime_ns > remote_mtime or (stat.st_mtime_ns == remote_mtime and self.config.node_id < peer_id)
                    if local_wins:
                        saved = self.transport.ssh(peer_id, ["save-conflict", rel, self.config.node_id])
                        self._record_conflict(rel, self.config.node_id, peer_id, stat.st_mtime_ns, remote_mtime,
                                              f"remote:{peer_id}:{saved['saved']}")
                    else:
                        saved = self._copy_conflict(source, rel, self.config.node_id)
                        self._record_conflict(rel, peer_id, self.config.node_id, remote_mtime, stat.st_mtime_ns,
                                              saved.relative_to(self.config.root).as_posix())
                        self._pull(peer_id, rel)
                        continue
                self._snapshot(source, rel)
                self.queued_pushes.setdefault(peer_id, {})[rel] = (digest, stat.st_mtime_ns, stat.st_size,
                                                                   statmod.S_IMODE(stat.st_mode))
            except Exception as exc: self._peer_error(peer_id, rel, exc)
        stat = source.lstat(); digest = self.state.fingerprint(source, rel, self.config.max_hash_size_mb, force=force_hash)
        for batch in self.queued_pushes.values():
            if rel in batch: batch[rel] = (digest, stat.st_mtime_ns, stat.st_size, statmod.S_IMODE(stat.st_mode))
        self.state.record_file(rel, stat.st_size, stat.st_mtime_ns, digest, synced=False)
        for peer_id in tuple(self.confirmed_peers.get(rel, ())): self._confirm_peer(rel, peer_id, digest)
        if not self.config.peers:
            self.state.db.execute("UPDATE files SET synced_sha256=? WHERE path=?", (digest, rel)); self.state.db.commit()
        self.last_sync = time.time()

    def _record_conflict(self, rel, winner, loser, win_mtime, lose_mtime, saved_path):
        self.state.db.execute("INSERT INTO conflicts(ts,path,winner,loser,winner_mtime,loser_mtime,saved_path) VALUES(?,?,?,?,?,?,?)",
            (time.time(), rel, winner, loser, win_mtime, lose_mtime, saved_path)); self.state.db.commit()
        self.log("warning", "conflict", rel, result=winner, detail=f"conflit avec {loser}; version perdante conservée")

    def _pull(self, peer_id, rel):
        source = self.safe_path(rel)
        old_digest = self.state.fingerprint(source, rel, self.config.max_hash_size_mb) if source.exists() or source.is_symlink() else None
        temp = self.config.root / ".dfr" / "tmp" / rel
        temp.parent.mkdir(parents=True, exist_ok=True)
        target = self.config.peers[peer_id]
        cmd = ["rsync", "-a", "--partial", "-e", f"ssh -i {self.config.ssh_key} -o BatchMode=yes -o StrictHostKeyChecking=yes -o ControlMaster=auto -o ControlPersist=60 -o ControlPath=/var/lib/dfr/.ssh/cm-%C",
               f"{target}:{self.config.root.as_posix()}/{rel}", str(temp)]
        result = __import__("subprocess").run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode: raise RuntimeError(result.stderr.strip() or "rsync pull en échec")
        source.parent.mkdir(parents=True, exist_ok=True)
        temp.replace(source)
        stat = source.lstat(); digest = self.state.fingerprint(source, rel, self.config.max_hash_size_mb, force=True)
        self._snapshot(source, rel); self.state.record_file(rel, stat.st_size, stat.st_mtime_ns, digest, synced=False)
        previous_confirmed = self.confirmed_peers.pop(rel, set()) if old_digest != digest else set()
        for other_peer, batch in self.queued_pushes.items():
            if rel in batch: batch[rel] = (digest, stat.st_mtime_ns, stat.st_size, statmod.S_IMODE(stat.st_mode))
        for other_peer in previous_confirmed - {peer_id}:
            self.queued_pushes.setdefault(other_peer, {})[rel] = (digest, stat.st_mtime_ns, stat.st_size,
                                                                   statmod.S_IMODE(stat.st_mode))
        self._confirm_peer(rel, peer_id, digest)

    def _peer_error(self, peer, rel, exc):
        self.state.db.execute("UPDATE peers SET online=0,last_error=? WHERE id=?", (str(exc)[:500], peer)); self.state.db.commit()
        count = self.retries.get((peer, rel), 0) + 1; self.retries[(peer, rel)] = count
        delay = min((5, 15, 60, 300)[min(count-1, 3)], 300)
        self.pending[rel] = time.monotonic() + delay
        self.log("error", "sync_error", rel, peer, "retry", detail=str(exc))

    def flush_transfers(self):
        batches, self.queued_pushes = self.queued_pushes, {}
        for peer_id, items in batches.items():
            try:
                self.transport.push_batch(peer_id, items)
                for rel, (digest, _mtime, _size, _mode) in items.items():
                    self.retries.pop((peer_id, rel), None)
                    self._confirm_peer(rel, peer_id, digest)
            except Exception as exc:
                for rel in items: self._peer_error(peer_id, rel, exc)

    def _confirm_peer(self, rel: str, peer_id: str, digest: str):
        confirmed = self.confirmed_peers.setdefault(rel, set())
        confirmed.add(peer_id)
        if set(self.config.peers).issubset(confirmed):
            if self.state.file(rel) is not None:
                self.state.db.execute("UPDATE files SET synced_sha256=? WHERE path=?", (digest, rel))
                self.state.db.commit()
                self.confirmed_peers.pop(rel, None)

    def _handle(self, kind, rel, cookie):
        if kind == "overflow": self.log("error", "inotify_overflow", result="reconcile"); self.reconcile(); return
        if kind == "move_from":
            if self._consume_remote_delete(rel): return
            self.move_from[cookie] = (rel, time.monotonic()); return
        if kind == "move_to":
            previous = self.move_from.pop(cookie, None)
            if previous:
                old = previous[0]
                if self._consume_remote_rename(old, rel):
                    self.log("debug", "rename_applied", rel, result="remote")
                    return
                self._rename_state(old, rel)
                for peer in self.config.peers:
                    try:
                        self.transport.rename(peer, old, rel)
                        self.retries.pop((peer, rel), None)
                        row = self.state.file(rel)
                        if row: self._confirm_peer(rel, peer, row["sha256"])
                    except Exception as exc: self._peer_error(peer, rel, exc)
                self.pending[rel] = time.monotonic() + self.config.debounce_ms / 1000
                self.log("info", "rename", rel, result=old)
                return
        if kind == "delete":
            if self._consume_remote_delete(rel): return
            if self.state.db.execute("SELECT 1 FROM tombstones WHERE path=?", (rel,)).fetchone() and not self.state.file(rel):
                return
            self.pending[rel] = time.monotonic() + self.config.debounce_ms / 1000
            return
        if kind in ("write", "attrib", "move_to", "symlink"):
            self.pending[rel] = time.monotonic() + self.config.debounce_ms / 1000

    def _consume_remote_rename(self, old: str, new: str) -> bool:
        marker_dir = self.config.root / ".dfr" / "applying"
        if not marker_dir.exists(): return False
        for marker in marker_dir.glob("rename-*"):
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
                if data.get("expires", 0) < time.time():
                    marker.unlink(); continue
                if data.get("old") == old and data.get("new") == new:
                    marker.unlink()
                    return True
            except (OSError, ValueError):
                continue
        return False

    def _consume_remote_delete(self, rel: str) -> bool:
        marker_dir = self.config.root / ".dfr" / "applying"
        if not marker_dir.exists(): return False
        for marker in marker_dir.glob("delete-*"):
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
                if data.get("expires", 0) < time.time():
                    marker.unlink(); continue
                if data.get("path") == rel:
                    marker.unlink(); return True
            except (OSError, ValueError):
                continue
        return False

    def _rename_state(self, old: str, new: str):
        for table in ("files", "tombstones"):
            self.state.db.execute(
                f"UPDATE {table} SET path=? || substr(path, ?) WHERE path=? OR substr(path,1,?)=?",
                (new, len(old) + 1, old, len(old) + 1, old + "/"),
            )
        self.state.db.commit()
        source = self.config.root / ".dfr" / "versions" / old
        destination = self.config.root / ".dfr" / "versions" / new
        if source.exists() or source.is_symlink():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                if destination.is_dir() and not destination.is_symlink(): shutil.rmtree(destination)
                else: destination.unlink()
            source.rename(destination)
            return

    def _apply_remote_tombstone(self, peer_id: str, rel: str, tombstone: dict):
        path = self.safe_path(rel)
        mtime_ns = int(tombstone["mtime_ns"])
        node = str(tombstone["node"])
        current = self.state.db.execute(
            "SELECT mtime_ns,node FROM tombstones WHERE path=?", (rel,)
        ).fetchone()
        current_wins = bool(current and (
            int(current["mtime_ns"]) > mtime_ns
            or (int(current["mtime_ns"]) == mtime_ns and current["node"] <= node)
        ))
        if current_wins:
            mtime_ns = int(current["mtime_ns"])
            node = current["node"]

        if path.exists() or path.is_symlink():
            st = path.lstat()
            if statmod.S_ISDIR(st.st_mode) and not path.is_symlink():
                # Directory tombstones are merged with their child tombstones below.
                pass
            elif st.st_mtime_ns > mtime_ns:
                self.pending[rel] = time.monotonic()
                return
            else:
                trash = self.config.trash_dir / f"{time.time_ns()}-{rel}"
                trash.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(trash))

        if current_wins:
            self.state.db.execute("DELETE FROM files WHERE path=?", (rel,))
            self.state.db.commit()
        else:
            self.state.add_tombstone(rel, mtime_ns, node)

        for batch in self.queued_pushes.values():
            batch.pop(rel, None)
        self.confirmed_peers.pop(rel, None)
        for other_peer in self.config.peers:
            if other_peer == peer_id:
                continue
            try:
                result = self.transport.delete(other_peer, rel, mtime_ns, node)
                if result.get("newer"):
                    self.pending[rel] = time.monotonic()
            except Exception as exc:
                self._peer_error(other_peer, rel, exc)
        self.log("info", "apply_delete", rel, peer_id, "ok", detail=f"tombstone de {node}")

    def reconcile(self):
        if self.paused: return
        remote_indexes = {}
        remote_tombstones = {}
        for peer_id in self.config.peers:
            try:
                snapshot = self.transport.list(peer_id)
                self.retries.pop((peer_id, ""), None)
                remote_indexes[peer_id] = {entry["path"]: entry for entry in snapshot.get("files", [])}
                remote_tombstones[peer_id] = snapshot.get("tombstones", [])
                self.state.db.execute("UPDATE peers SET online=1,last_error='',last_success=? WHERE id=?", (time.time(), peer_id))
            except Exception as exc:
                self._peer_error(peer_id, "", exc)
        local_paths = set()
        for base, dirs, files in os.walk(self.config.root, followlinks=False):
            link_dirs = [d for d in dirs if (Path(base) / d).is_symlink()]
            dirs[:] = [d for d in dirs if d != ".dfr" and d not in link_dirs]
            files.extend(link_dirs)
            for name in files:
                path = Path(base) / name
                try:
                    rel = path.relative_to(self.config.root).as_posix()
                    if any(fnmatch.fnmatch(path.name, pattern) for pattern in self.config.excludes): continue
                    local_paths.add(rel); self._sync_path(rel)
                except Exception as exc: self.log("error", "reconcile_error", str(path), detail=str(exc))
        for row in self.state.db.execute("SELECT path FROM files").fetchall():
            rel = row["path"]
            if rel not in local_paths:
                existing = self.config.root / rel
                if existing.exists() or existing.is_symlink(): continue
                try: self._sync_path(rel)
                except Exception as exc: self.log("error", "reconcile_delete_error", rel, detail=str(exc))
        for peer_id, index in remote_indexes.items():
            for rel, metadata in index.items():
                if rel in local_paths: continue
                try:
                    self.safe_path(rel)
                    tomb = self.state.db.execute("SELECT mtime_ns,node FROM tombstones WHERE path=?", (rel,)).fetchone()
                    if tomb and int(tomb[0]) >= int(metadata.get("mtime_ns", 0)):
                        self.transport.delete(peer_id, rel, int(tomb["mtime_ns"]), tomb["node"])
                    else:
                        self._pull(peer_id, rel)
                        local_paths.add(rel)
                        self._sync_path(rel)
                        self.log("info", "bootstrap_receive", rel, peer_id, "ok")
                except Exception as exc:
                    self._peer_error(peer_id, rel, exc)
        for peer_id, tombstones in remote_tombstones.items():
            for tombstone in tombstones:
                try:
                    rel = str(tombstone["path"])
                    self.safe_path(rel)
                    self._apply_remote_tombstone(peer_id, rel, tombstone)
                except Exception as exc:
                    self._peer_error(peer_id, str(tombstone.get("path", "")), exc)
        self._cleanup_retained_data()
        self.flush_transfers()
        self.initial_reconcile = False
        self.log("info", "reconcile", result="completed")

    def _cleanup_retained_data(self):
        cutoff = time.time() - self.config.trash_retention_days * 86400
        log_cutoff = time.time() - self.config.log_retention_days * 86400
        self.state.db.execute("DELETE FROM tombstones WHERE created<?", (cutoff,))
        self.state.db.execute("DELETE FROM operations WHERE ts<?", (log_cutoff,))
        self.state.db.commit()
        if self.config.trash_dir.exists():
            for base, dirs, files in os.walk(self.config.trash_dir, topdown=False):
                for name in files:
                    item = Path(base) / name
                    try:
                        if item.lstat().st_mtime < cutoff: item.unlink()
                    except FileNotFoundError: pass
                for name in dirs:
                    item = Path(base) / name
                    try: item.rmdir()
                    except OSError: pass

    def run(self):
        self.config.socket.parent.mkdir(parents=True, exist_ok=True)
        try: self.config.socket.unlink()
        except FileNotFoundError: pass
        server = ControlServer(self.config.socket, self)
        os.chmod(self.config.socket, 0o660)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGHUP, lambda *_: setattr(self, "reload_requested", True))
        self.log("info", "startup", result="ok")
        self.reconcile()
        next_reconcile = time.monotonic() + self.config.reconcile_interval_s
        while self.running:
            if self.reload_requested:
                self.reload_requested = False
                try: self.reload_config()
                except Exception as exc: self.log("error", "reload_error", detail=str(exc))
            for kind, rel, cookie in self.watcher.read(): self._handle(kind, rel, cookie)
            now = time.monotonic()
            for cookie, (old_rel, moved_at) in list(self.move_from.items()):
                if now - moved_at >= 0.5:
                    self.move_from.pop(cookie, None)
                    self.pending[old_rel] = now + self.config.debounce_ms / 1000
            if not self.paused:
                for rel, due in list(self.pending.items()):
                    if now >= due:
                        self.pending.pop(rel, None)
                        if not rel:
                            self.reconcile_requested = True
                            continue
                        try: self._sync_path(rel, force_hash=True)
                        except Exception as exc: self.log("error", "sync_error", rel, detail=str(exc))
                self.flush_transfers()
                if now >= next_reconcile:
                    self.reconcile(); next_reconcile = now + self.config.reconcile_interval_s
            if self.reconcile_requested and not self.paused:
                self.reconcile_requested = False
                self.reconcile(); next_reconcile = now + self.config.reconcile_interval_s
            time.sleep(0.05)
        server.shutdown(); server.server_close(); self.watcher.close(); self.state.close()
        try: self.config.socket.unlink()
        except FileNotFoundError: pass
