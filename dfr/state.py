"""SQLite state, tombstones and operation log."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


class State:
    def __init__(self, filename: Path):
        filename.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(filename, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL, synced_sha256 TEXT,
          origin TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tombstones(path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL,
          node TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(id INTEGER PRIMARY KEY, ts REAL NOT NULL,
          level TEXT NOT NULL, node TEXT NOT NULL, event TEXT NOT NULL, path TEXT NOT NULL DEFAULT '',
          peer TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0,
          bytes INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS peers(id TEXT PRIMARY KEY, target TEXT NOT NULL,
          online INTEGER NOT NULL DEFAULT 0, last_success REAL, last_error TEXT NOT NULL DEFAULT '',
          clock_delta REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS conflicts(id INTEGER PRIMARY KEY, ts REAL NOT NULL, path TEXT NOT NULL,
          winner TEXT NOT NULL, loser TEXT NOT NULL, winner_mtime INTEGER NOT NULL,
          loser_mtime INTEGER NOT NULL, saved_path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()

    @staticmethod
    def digest(path: Path, full_limit: int = 0) -> str:
        if path.is_symlink():
            return hashlib.sha256(os.fsencode(path.readlink())).hexdigest()
        h = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    def fingerprint(self, path: Path, rel: str, full_hash_limit_mb: int, force: bool = False) -> str:
        st = path.lstat()
        if path.is_symlink(): return self.digest(path)
        if st.st_size > full_hash_limit_mb * 1024 * 1024:
            return f"meta:{st.st_size}:{st.st_mtime_ns}"
        indexed = self.file(rel)
        if not force and indexed and indexed["size"] == st.st_size and indexed["mtime_ns"] == st.st_mtime_ns:
            return indexed["sha256"]
        return self.digest(path)

    def file(self, rel: str):
        return self.db.execute("SELECT * FROM files WHERE path=?", (rel,)).fetchone()

    def record_file(self, rel: str, size: int, mtime_ns: int, digest: str, *, synced: bool = True, origin: str = ""):
        self.db.execute("""INSERT INTO files(path,size,mtime_ns,sha256,synced_sha256,origin,updated)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET size=excluded.size,
          mtime_ns=excluded.mtime_ns,sha256=excluded.sha256,
          synced_sha256=CASE WHEN ? THEN excluded.sha256 ELSE files.synced_sha256 END,
          origin=excluded.origin,updated=excluded.updated""",
          (rel, size, mtime_ns, digest, digest if synced else None, origin, time.time(), int(synced)))
        self.db.execute("DELETE FROM tombstones WHERE path=?", (rel,))
        self.db.commit()

    def add_tombstone(self, rel: str, mtime_ns: int, node: str):
        self.db.execute("""INSERT INTO tombstones(path,mtime_ns,node,created) VALUES(?,?,?,?)
          ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns,node=excluded.node,created=excluded.created
          WHERE excluded.mtime_ns>tombstones.mtime_ns
             OR (excluded.mtime_ns=tombstones.mtime_ns AND excluded.node<tombstones.node)""",
          (rel, mtime_ns, node, time.time()))
        self.db.execute("DELETE FROM files WHERE path=?", (rel,))
        self.db.commit()

    def log(self, node: str, level: str, event: str, path: str = "", peer: str = "", result: str = "", duration_ms: int = 0, size: int = 0, detail: str = ""):
        self.db.execute("INSERT INTO operations(ts,level,node,event,path,peer,result,duration_ms,bytes,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), level, node, event, path, peer, result, duration_ms, size, detail[:2000]))
        self.db.commit()

    def logs(self, limit: int = 200, offset: int = 0, filters: dict[str, Any] | None = None):
        filters = filters or {}
        where, params = [], []
        for key, col in (("node", "node"), ("type", "event"), ("level", "level"), ("peer", "peer")):
            if filters.get(key): where.append(f"{col}=?"); params.append(filters[key])
        for key, op in (("from", ">="), ("to", "<=")):
            if filters.get(key): where.append(f"ts{op}?"); params.append(float(filters[key]))
        sql = "SELECT * FROM operations" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [min(max(int(limit), 1), 1000), max(int(offset), 0)]
        return [dict(row) for row in self.db.execute(sql, params)]

    def peers_status(self): return [dict(row) for row in self.db.execute("SELECT * FROM peers ORDER BY id")]
    def conflicts(self, limit=100): return [dict(row) for row in self.db.execute("SELECT * FROM conflicts ORDER BY id DESC LIMIT ?", (limit,))]
    def close(self): self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
