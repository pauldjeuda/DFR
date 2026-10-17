"""Recursive Linux inotify wrapper using libc, with no Python package dependency."""
from __future__ import annotations

import ctypes
import errno
import os
import struct
import time
from pathlib import Path

libc = ctypes.CDLL(None, use_errno=True) if os.name == "posix" else None
IN_CLOSE_WRITE = 0x00000008
IN_ATTRIB = 0x00000004
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ISDIR = 0x40000000
MASK = IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
EVENT = struct.Struct("iIII")


class Watcher:
    def __init__(self, root: Path, excluded):
        if not hasattr(os, "O_NONBLOCK") or os.name != "posix":
            raise RuntimeError("le démon DFR nécessite Linux et inotify")
        self.root = root.resolve()
        self.excluded = excluded
        self.fd = libc.inotify_init1(os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        if self.fd < 0: raise OSError(ctypes.get_errno(), "inotify_init1")
        self.watches = {}
        self.move_cookies = {}
        self.watch_error = False
        self.add_tree(self.root)

    def ignored(self, p: Path):
        if ".dfr" in p.parts: return True
        import fnmatch
        return any(fnmatch.fnmatch(p.name, pat) for pat in self.excluded)

    def add(self, directory: Path):
        wd = libc.inotify_add_watch(self.fd, os.fsencode(directory), MASK)
        if wd < 0:
            code = ctypes.get_errno()
            if code in (errno.ENOSPC, errno.EMFILE):
                self.watch_error = True
                return
            raise OSError(code, f"inotify_add_watch {directory}")
        self.watches[wd] = directory

    def add_tree(self, directory: Path):
        self.add(directory)
        for base, dirs, _ in os.walk(directory, followlinks=False):
            dirs[:] = [d for d in dirs if not self.ignored(Path(base) / d) and not (Path(base) / d).is_symlink()]
            for name in dirs:
                path = Path(base) / name
                if path != directory: self.add(path)

    def read(self):
        events = []
        if self.watch_error:
            events.append(("overflow", "", 0)); self.watch_error = False
        try: data = os.read(self.fd, 1024 * 256)
        except BlockingIOError: return events
        offset = 0
        while offset + EVENT.size <= len(data):
            wd, mask, cookie, length = EVENT.unpack_from(data, offset); offset += EVENT.size
            name = data[offset:offset + length].split(b"\0", 1)[0].decode(errors="surrogateescape"); offset += length
            if mask & IN_Q_OVERFLOW:
                events.append(("overflow", "", cookie)); continue
            base = self.watches.get(wd)
            if base is None: continue
            path = base / name if name else base
            if mask & IN_IGNORED:
                self.watches.pop(wd, None)
                continue
            try: rel = path.relative_to(self.root).as_posix()
            except ValueError: continue
            if self.ignored(path): continue
            if mask & IN_MOVED_FROM and mask & IN_ISDIR:
                self.move_cookies[cookie] = (path, time.monotonic())
            if mask & IN_MOVED_TO and mask & IN_ISDIR:
                moved = self.move_cookies.pop(cookie, None)
                if moved:
                    old_path = moved[0]
                    for watch_id, watched_path in list(self.watches.items()):
                        try: suffix = watched_path.relative_to(old_path)
                        except ValueError: continue
                        self.watches[watch_id] = path / suffix
            if mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO) and path.is_dir() and not path.is_symlink():
                try: self.add_tree(path)
                except OSError: pass
            kind = ("symlink" if mask & IN_CREATE and path.is_symlink() else
                    "write" if mask & IN_CLOSE_WRITE else "attrib" if mask & IN_ATTRIB else "delete" if mask & IN_DELETE else
                    "move_from" if mask & IN_MOVED_FROM else "move_to" if mask & IN_MOVED_TO else
                    "create_dir" if mask & IN_CREATE and mask & IN_ISDIR else "create")
            events.append((kind, rel, cookie))
        for cookie, (old_path, moved_at) in list(self.move_cookies.items()):
            if time.monotonic() - moved_at > 1:
                self.move_cookies.pop(cookie, None)
                for watch_id, watched_path in list(self.watches.items()):
                    if watched_path == old_path or old_path in watched_path.parents:
                        libc.inotify_rm_watch(self.fd, watch_id)
                        self.watches.pop(watch_id, None)
        return events

    def close(self): os.close(self.fd)
