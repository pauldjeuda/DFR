"""dfr-ctl: local daemon control and restricted SSH peer operations."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import socket
import stat as statmod
import sys
import time
from pathlib import Path

from .config import Config
from .state import State


def local_control(config: Config, command: str, args=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10); client.connect(str(config.socket))
        client.sendall((json.dumps({"command": command, "args": args or {}}) + "\n").encode())
        response = json.loads(client.makefile("r", encoding="utf-8").readline())
    if not response.get("ok"): raise RuntimeError(response.get("error", "commande refusée"))
    return response["result"]


def safe_path(config, rel, allow_internal=False):
    p = Path(rel)
    if not rel or p.is_absolute() or ".." in p.parts or "\x00" in rel: raise ValueError("chemin invalide")
    if not allow_internal and ".dfr" in p.parts: raise ValueError("chemin réservé au service DFR")
    root = config.root.resolve(); target = (root / p).resolve(strict=False)
    raw_target = root / p
    parent = raw_target.parent.resolve(strict=False)
    if parent != root and root not in parent.parents: raise ValueError("chemin hors racine")
    if raw_target.is_symlink(): return raw_target
    if target != root and root not in target.parents: raise ValueError("chemin hors racine")
    return target


def remote(config, operation, values):
    state = State(config.state_db)
    try:
        if operation == "list":
            seen = set()
            for base, dirs, files in os.walk(config.root, followlinks=False):
                dirs[:] = [d for d in dirs if d != ".dfr" and not (Path(base) / d).is_symlink()]
                names = list(files) + [d for d in os.listdir(base) if (Path(base) / d).is_symlink() and (Path(base) / d).is_dir()]
                for name in names:
                    p = Path(base) / name
                    try:
                        rel = p.relative_to(config.root).as_posix()
                        seen.add(rel)
                        if any(fnmatch.fnmatch(p.name, pattern) for pattern in config.excludes): continue
                        if p.is_dir() and not p.is_symlink(): continue
                        st = p.lstat()
                        if not (statmod.S_ISREG(st.st_mode) or statmod.S_ISLNK(st.st_mode)): continue
                        tomb = state.db.execute("SELECT mtime_ns FROM tombstones WHERE path=?", (rel,)).fetchone()
                        if tomb and int(tomb[0]) >= st.st_mtime_ns:
                            state.db.execute("DELETE FROM files WHERE path=?", (rel,)); state.db.commit()
                            continue
                        # Listing a peer must refresh its observed fingerprint without
                        # claiming that this version has been replicated everywhere.
                        state.record_file(rel, st.st_size, st.st_mtime_ns,
                                          state.fingerprint(p, rel, config.max_hash_size_mb), synced=False)
                    except (OSError, ValueError): continue
            for row in state.db.execute("SELECT path FROM files").fetchall():
                if row["path"] not in seen:
                    state.add_tombstone(row["path"], time.time_ns(), config.node_id)
            rows = [row for row in state.db.execute("SELECT path,size,mtime_ns,sha256 FROM files ORDER BY path").fetchall()
                    if not any(fnmatch.fnmatch(Path(row["path"]).name, pattern) for pattern in config.excludes)]
            tombs = state.db.execute("SELECT path,mtime_ns,node FROM tombstones").fetchall()
            return {"node": config.node_id, "server_time_ns": time.time_ns(),
                    "files": [dict(r) for r in rows], "tombstones": [dict(r) for r in tombs]}
        if operation == "stat":
            rel = values[0]; p = safe_path(config, rel)
            if not p.exists() and not p.is_symlink():
                tomb = state.db.execute("SELECT mtime_ns,node FROM tombstones WHERE path=?", (rel,)).fetchone()
                return {"exists": False, "tombstone": dict(tomb) if tomb else None,
                        "node": config.node_id, "server_time_ns": time.time_ns()}
            if p.is_dir() and not p.is_symlink(): return {"exists": True, "type": "directory", "node": config.node_id, "server_time_ns": time.time_ns()}
            st = p.lstat()
            if not (statmod.S_ISREG(st.st_mode) or statmod.S_ISLNK(st.st_mode)):
                return {"exists": False, "type": "special", "node": config.node_id, "server_time_ns": time.time_ns()}
            tomb = state.db.execute("SELECT mtime_ns,node FROM tombstones WHERE path=?", (rel,)).fetchone()
            return {"exists": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                    "sha256": state.fingerprint(p, rel, config.max_hash_size_mb), "type": "symlink" if p.is_symlink() else "file",
                    "mode": statmod.S_IMODE(st.st_mode), "tombstone": dict(tomb) if tomb else None,
                    "node": config.node_id, "server_time_ns": time.time_ns()}
        if operation == "hash":
            p = safe_path(config, values[0])
            if not p.exists() and not p.is_symlink(): raise FileNotFoundError(values[0])
            if p.is_symlink() or p.lstat().st_size <= config.max_hash_size_mb * 1024 * 1024:
                return {"sha256": state.fingerprint(p, values[0], config.max_hash_size_mb, force=True)}
            return {"sha256": f"meta:{p.lstat().st_size}:{p.lstat().st_mtime_ns}"}
        if operation == "begin-apply":
            p = safe_path(config, values[0]); (config.root / ".dfr" / "applying").mkdir(parents=True, exist_ok=True)
            marker = config.root / ".dfr" / "applying" / str(abs(hash(values[0])))
            marker.write_text(json.dumps({"path": values[0], "hash": values[1], "mtime_ns": int(values[2]), "expires": time.time()+60}), encoding="utf-8")
            return {"applying": True, "path": str(p)}
        if operation == "record":
            rel, digest, mtime_ns, size = values[:4]; p = safe_path(config, rel)
            state.record_file(rel, int(size), int(mtime_ns), digest, origin="peer")
            markerdir = config.root / ".dfr" / "applying"
            for marker in markerdir.glob("*") if markerdir.exists() else []:
                try:
                    data=json.loads(marker.read_text(encoding="utf-8"))
                    if data.get("path")==rel: marker.unlink()
                except (OSError, ValueError): pass
            return {"recorded": True, "exists": p.exists()}
        if operation == "apply-delete":
            rel, mtime_ns, node = values[:3]; p = safe_path(config, rel)
            mtime_ns = int(mtime_ns)
            existing_tombstone = state.db.execute(
                "SELECT mtime_ns,node FROM tombstones WHERE path=?", (rel,)
            ).fetchone()
            if existing_tombstone and (
                int(existing_tombstone["mtime_ns"]) > mtime_ns
                or (int(existing_tombstone["mtime_ns"]) == mtime_ns and existing_tombstone["node"] < node)
            ):
                mtime_ns = int(existing_tombstone["mtime_ns"])
                node = existing_tombstone["node"]
            if (p.exists() or p.is_symlink()) and p.lstat().st_mtime_ns > mtime_ns:
                return {"deleted": False, "newer": True, "mtime_ns": p.lstat().st_mtime_ns}
            applying = config.root / ".dfr" / "applying"; applying.mkdir(parents=True, exist_ok=True)
            marker = applying / f"delete-{time.time_ns()}"
            marker.write_text(json.dumps({"path": rel, "expires": time.time()+5}), encoding="utf-8")
            trash = config.trash_dir / f"{time.time_ns()}-{rel}"
            if p.exists() or p.is_symlink():
                trash.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(p), str(trash))
            backup = config.root / ".dfr" / "versions" / rel
            if backup.exists() or backup.is_symlink(): backup.unlink()
            state.add_tombstone(rel, mtime_ns, node)
            prefix = rel + "/"
            child_rows = state.db.execute("SELECT path FROM files WHERE substr(path,1,?)=?", (len(prefix), prefix)).fetchall()
            for child in child_rows:
                state.add_tombstone(child["path"], mtime_ns, node)
            state.log(config.node_id,"info","apply_delete",rel,result="ok",detail=f"tombstone de {node}")
            return {"deleted": True}
        if operation == "apply-rename":
            old, new, _node = values[:3]; src, dst = safe_path(config, old), safe_path(config, new)
            applying = config.root / ".dfr" / "applying"; applying.mkdir(parents=True, exist_ok=True)
            marker = applying / f"rename-{time.time_ns()}"
            marker.write_text(json.dumps({"old": old, "new": new, "expires": time.time()+30}), encoding="utf-8")
            if src.exists() or src.is_symlink(): dst.parent.mkdir(parents=True, exist_ok=True); src.rename(dst)
            for table in ("files", "tombstones"):
                state.db.execute(
                    f"UPDATE {table} SET path=? || substr(path, ?) WHERE path=? OR substr(path,1,?)=?",
                    (new, len(old) + 1, old, len(old) + 1, old + "/"),
                )
            state.db.commit()
            old_backup = config.root / ".dfr" / "versions" / old
            new_backup = config.root / ".dfr" / "versions" / new
            if old_backup.exists() or old_backup.is_symlink():
                new_backup.parent.mkdir(parents=True, exist_ok=True); old_backup.rename(new_backup)
            return {"renamed": True}
        if operation == "save-conflict":
            rel, node = values[:2]; p = safe_path(config, rel)
            if not p.exists() and not p.is_symlink(): raise FileNotFoundError(rel)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f"-{time.time_ns() % 1_000_000_000:09d}"
            saved = config.conflicts_dir / f"{stamp}-{node}" / rel
            saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, saved, follow_symlinks=False)
            return {"saved": str(saved.relative_to(config.root))}
        if operation == "restore-conflict":
            rel, archive_rel = values[:2]
            destination = safe_path(config, rel)
            archive = safe_path(config, archive_rel, allow_internal=True)
            if not archive.exists() and not archive.is_symlink(): raise FileNotFoundError("version conservée introuvable")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = config.root / ".dfr" / "tmp" / rel; temp.parent.mkdir(parents=True, exist_ok=True)
            if temp.exists() or temp.is_symlink(): temp.unlink()
            if archive.is_symlink(): temp.symlink_to(archive.readlink())
            else: shutil.copy2(archive, temp)
            temp.replace(destination); os.utime(destination, None, follow_symlinks=not destination.is_symlink())
            return {"restored": True}
        raise ValueError("opération distante refusée")
    finally: state.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dfr-ctl")
    parser.add_argument("--config", default="/etc/dfr/dfr.conf")
    parser.add_argument("--remote-json", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check", action="store_true", help="valide la configuration")
    parser.add_argument("command", nargs="?")
    parser.add_argument("args", nargs="*")
    opts, extra = parser.parse_known_args(argv)
    if extra: opts.args.extend(extra)
    try:
        config = Config.load(opts.config)
        if opts.check:
            print(f"Configuration valide : nœud={config.node_id}, racine={config.root}, pairs={len(config.peers)}"); return 0
        if opts.remote_json:
            result = remote(config, opts.command or "", opts.args)
            print(json.dumps(result, ensure_ascii=False)); return 0
        cmd = opts.command or "status"
        if cmd in ("status", "peers", "conflicts"): result = local_control(config, cmd)
        elif cmd == "logs":
            ap=argparse.ArgumentParser(prog="dfr-ctl logs", add_help=False); ap.add_argument("--limit",type=int,default=200); ap.add_argument("--offset",type=int,default=0)
            ap.add_argument("--node"); ap.add_argument("--type"); ap.add_argument("--level"); ap.add_argument("--from",dest="from_"); ap.add_argument("--to")
            f=ap.parse_args(opts.args); result=local_control(config,"logs",{"limit":f.limit,"offset":f.offset,"node":f.node,"type":f.type,"level":f.level,"from":f.from_,"to":f.to})
        elif cmd in ("pause", "resume", "resync", "reload"): result = local_control(config, cmd)
        elif cmd == "restore":
            if len(opts.args) != 1: raise ValueError("usage : dfr-ctl restore ID_CONFLIT")
            result = local_control(config, "restore-conflict", {"id": int(opts.args[0])})
        else: raise ValueError(f"commande inconnue : {cmd}")
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except Exception as exc:
        print(f"dfr-ctl: {exc}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
