"""Validated INI configuration for a DFR node."""
from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    node_id: str
    root: Path
    debounce_ms: int = 1000
    reconcile_interval_s: int = 300
    max_hash_size_mb: int = 100
    trash_retention_days: int = 7
    excludes: tuple[str, ...] = ("*.swp", "*.tmp", ".~lock.*")
    peers: dict[str, str] = field(default_factory=dict)
    ssh_key: Path = Path("/var/lib/dfr/.ssh/id_ed25519")
    log_level: str = "info"
    log_retention_days: int = 30
    bind: str = "127.0.0.1:8080"
    state_db: Path = Path("/var/lib/dfr/state.db")
    log_file: Path = Path("/var/log/dfr/dfr.log")
    socket: Path = Path("/run/dfr/dfr.sock")
    trash_dir: Path | None = None
    conflicts_dir: Path | None = None

    @classmethod
    def load(cls, filename: str | Path) -> "Config":
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        path = Path(filename)
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        for section in ("node", "sync", "peers", "ssh", "logging", "web"):
            if not parser.has_section(section):
                raise ValueError(f"section [{section}] manquante")
        node_id = parser.get("node", "id").strip()
        root = Path(parser.get("node", "root")).expanduser()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", node_id):
            raise ValueError("[node] id doit être un identifiant simple")
        if not root.is_absolute():
            raise ValueError("[node] root doit être un chemin absolu")
        peers = {key.strip(): value.strip() for key, value in parser.items("peers")}
        if node_id in peers:
            raise ValueError("le nœud local ne peut pas être déclaré comme son propre pair")
        if len(peers) > 4:
            raise ValueError("DFR v1 accepte au maximum 4 pairs")
        for peer_id, target in peers.items():
            if not peer_id or not re.fullmatch(r"[A-Za-z0-9_.-]+", peer_id):
                raise ValueError(f"identifiant de pair invalide : {peer_id}")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+@(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])", target):
                raise ValueError(f"déclaration de pair invalide : {peer_id}")
        debounce = parser.getint("sync", "debounce_ms", fallback=1000)
        interval = parser.getint("sync", "reconcile_interval_s", fallback=300)
        hash_limit = parser.getint("sync", "max_hash_size_mb", fallback=100)
        retention = parser.getint("sync", "trash_retention_days", fallback=7)
        if min(debounce, interval, hash_limit, retention) < 1:
            raise ValueError("les seuils [sync] doivent être supérieurs à zéro")
        excludes = tuple(x.strip() for x in parser.get("sync", "exclude", fallback="*.swp, *.tmp, .~lock.*").split(",") if x.strip())
        ssh_key = Path(parser.get("ssh", "key")).expanduser()
        state_db = Path(parser.get("node", "state_db", fallback="/var/lib/dfr/state.db")).expanduser()
        log_file = Path(parser.get("logging", "file", fallback="/var/log/dfr/dfr.log")).expanduser()
        control_socket = Path(parser.get("web", "socket", fallback="/run/dfr/dfr.sock")).expanduser()
        if any(not p.is_absolute() for p in (ssh_key, state_db, log_file, control_socket)):
            raise ValueError("les chemins ssh.key, node.state_db, logging.file et web.socket doivent être absolus")
        log_level = parser.get("logging", "level", fallback="info").lower()
        if log_level not in {"debug", "info", "warning", "error"}:
            raise ValueError("[logging] level doit être debug, info, warning ou error")
        bind = parser.get("web", "bind", fallback="127.0.0.1:8080")
        host, separator, port_text = bind.rpartition(":")
        if not separator or not host.strip("[]") or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise ValueError("web.bind doit être hôte:port")
        log_retention = parser.getint("logging", "retention_days", fallback=30)
        if log_retention < 1: raise ValueError("logging.retention_days doit être supérieur à zéro")
        return cls(
            node_id=node_id, root=root, debounce_ms=debounce,
            reconcile_interval_s=interval, max_hash_size_mb=hash_limit,
            trash_retention_days=retention, excludes=excludes, peers=peers,
            ssh_key=ssh_key, log_level=log_level,
            log_retention_days=log_retention,
            bind=bind,
            state_db=state_db,
            log_file=log_file,
            socket=control_socket,
            trash_dir=root / ".dfr" / "trash", conflicts_dir=root / ".dfr" / "conflicts",
        )
