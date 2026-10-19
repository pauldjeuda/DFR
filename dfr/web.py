"""Local Flask dashboard and REST API."""
from __future__ import annotations

import configparser
import csv
import io
import json
import os
import re
import shutil
import socket
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request, send_file

from .config import Config


def control(config, command, args=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10); client.connect(str(config.socket))
        client.sendall((json.dumps({"command": command, "args": args or {}})+"\n").encode())
        result=json.loads(client.makefile("r",encoding="utf-8").readline())
    if not result.get("ok"): raise RuntimeError(result.get("error","échec du contrôle local"))
    return result["result"]


def create_app(config_path="/etc/dfr/dfr.conf"):
    app=Flask(__name__, template_folder="templates", static_folder="static")
    config_file = Path(os.environ.get("DFR_CONFIG", config_path))
    config=Config.load(config_file); app.config["DFR_CONFIG"]=config
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
    text_limit = 1024 * 1024

    def file_path(relative, *, allow_root=False, allow_symlink=False):
        """Resolve a UI path inside the shared root without traversing symlinks."""
        if not isinstance(relative, str) or "\x00" in relative:
            raise ValueError("chemin invalide")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or ".dfr" in pure.parts or "\\" in relative:
            raise ValueError("chemin hors de la racine ou réservé au service DFR")
        parts = tuple(part for part in pure.parts if part not in ("", "."))
        if not parts:
            if allow_root:
                return config.root.resolve()
            raise ValueError("la racine partagée ne peut pas être modifiée")
        root = config.root.resolve()
        current = root
        for index, part in enumerate(parts):
            current = current / part
            final = index == len(parts) - 1
            if current.is_symlink():
                if final and allow_symlink:
                    return current
                raise ValueError("les liens symboliques ne peuvent pas être traversés depuis l’interface")
            resolved = current.resolve(strict=False)
            if resolved != root and root not in resolved.parents:
                raise ValueError("chemin hors de la racine partagée")
            current = resolved
        return current

    def child_path(parent, name):
        if not isinstance(name, str) or not name or name in {".", "..", ".dfr"} or "/" in name or "\\" in name or "\x00" in name:
            raise ValueError("nom d’élément invalide")
        if len(os.fsencode(name)) > 255:
            raise ValueError("nom trop long (255 octets maximum)")
        return file_path(f"{parent}/{name}" if parent else name)

    def write_atomic(destination, stream, limit, *, overwrite=True):
        destination.parent.mkdir(parents=False, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".dfr-upload-", suffix=".tmp", dir=destination.parent)
        try:
            total = 0
            with os.fdopen(fd, "wb") as output:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("fichier trop volumineux")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if overwrite:
                os.replace(temp_name, destination)
            else:
                os.link(temp_name, destination)
                os.unlink(temp_name)
        except Exception:
            try: os.unlink(temp_name)
            except FileNotFoundError: pass
            raise

    def save_peers(rows):
        if not isinstance(rows, list) or len(rows) > 4:
            raise ValueError("la liste doit contenir au maximum quatre pairs")
        peers = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("déclaration de pair invalide")
            peer_id = row.get("id", "")
            target = row.get("target", "")
            if not isinstance(peer_id, str) or peer_id.lower() != peer_id or not re.fullmatch(r"[a-z0-9_.-]+", peer_id):
                raise ValueError("identifiant de pair invalide (minuscules, chiffres, point, tiret ou souligné)")
            if peer_id == config.node_id:
                raise ValueError("le nœud local ne peut pas être déclaré comme son propre pair")
            if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+@(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])", target):
                raise ValueError("cible invalide : utilisez utilisateur@hôte")
            if peer_id in peers:
                raise ValueError("identifiant de pair en double")
            peers[peer_id] = target
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        with config_file.open(encoding="utf-8") as source:
            parser.read_file(source)
        if not parser.has_section("peers"):
            parser.add_section("peers")
        parser.remove_section("peers")
        parser.add_section("peers")
        for peer_id, target in peers.items():
            parser.set("peers", peer_id, target)
        fd, temp_name = tempfile.mkstemp(prefix=".dfr-config-", suffix=".tmp", dir=config_file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
                parser.write(output)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temp_name, stat.S_IMODE(config_file.stat().st_mode))
            Config.load(temp_name)
            os.replace(temp_name, config_file)
        except Exception:
            try: os.unlink(temp_name)
            except FileNotFoundError: pass
            raise
        return [{"id": peer_id, "target": target} for peer_id, target in sorted(peers.items())]

    @app.before_request
    def protect_local_actions():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
            return jsonify({"error": "requête intersite refusée"}), 403
        origin = request.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != request.host.casefold():
                return jsonify({"error": "origine non autorisée"}), 403
        return None

    @app.after_request
    def audit_request(response):
        try: control(config,"audit",{"path":request.path,"status":response.status_code})
        except OSError: pass
        return response

    @app.get("/")
    def index(): return render_template("index.html")

    @app.get("/api/status")
    def status(): return jsonify(control(config,"status"))

    @app.get("/api/nodes")
    def nodes(): return jsonify(control(config,"peers"))

    @app.get("/api/config/peers")
    def configured_peers():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(config_file, encoding="utf-8")
        return jsonify([{"id": peer_id, "target": target} for peer_id, target in sorted(parser.items("peers"))])

    @app.put("/api/config/peers")
    def update_peers():
        try:
            data = request.get_json(force=True)
            peers = save_peers(data.get("peers"))
            result = control(config, "reload")
            return jsonify({"peers": peers, "reload": result}), 202
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/logs")
    def logs():
        filters={k:request.args.get(k) for k in ("from","to","node","type","level","peer") if request.args.get(k)}
        rows=control(config,"logs",{"limit":request.args.get("limit",200),"offset":request.args.get("offset",0),**filters})
        return jsonify({"items":rows,"limit":request.args.get("limit",200),"offset":request.args.get("offset",0)})

    @app.get("/api/logs/export")
    def export_logs():
        fmt=request.args.get("format","json").lower()
        filters={k:request.args.get(k) for k in ("from","to","node","type","level","peer") if request.args.get(k)}
        rows=control(config,"logs",{"limit":1000,"offset":0,**filters})
        if fmt=="json": return Response(json.dumps(rows,ensure_ascii=False),mimetype="application/json",headers={"Content-Disposition":"attachment; filename=dfr-logs.json"})
        if fmt!="csv": return jsonify({"error":"format attendu : csv ou json"}),400
        out=io.StringIO(); cols=["ts","level","node","event","path","peer","result","duration_ms","bytes","detail"]
        writer=csv.DictWriter(out,fieldnames=cols); writer.writeheader()
        for row in rows:
            row["ts"]=datetime.fromtimestamp(row["ts"],timezone.utc).isoformat(); writer.writerow(row)
        return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=dfr-logs.csv"})

    @app.get("/api/conflicts")
    def conflicts(): return jsonify(control(config,"conflicts"))

    @app.get("/api/files")
    def list_files():
        try:
            directory = file_path(request.args.get("path", ""), allow_root=True)
            if not directory.is_dir(): return jsonify({"error": "répertoire introuvable"}), 404
            entries = []
            for item in directory.iterdir():
                if item.name == ".dfr":
                    continue
                mode = item.lstat().st_mode
                kind = "symlink" if stat.S_ISLNK(mode) else "directory" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "other"
                if kind == "other":
                    continue
                entries.append({"name": item.name, "path": item.relative_to(config.root.resolve()).as_posix(),
                                "type": kind, "size": item.lstat().st_size if kind == "file" else 0,
                                "modified": item.lstat().st_mtime})
            entries.sort(key=lambda entry: (entry["type"] != "directory", entry["name"].casefold(), entry["name"]))
            path = "" if directory == config.root.resolve() else directory.relative_to(config.root.resolve()).as_posix()
            return jsonify({"root": str(config.root), "path": path, "items": entries})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OSError as exc:
            return jsonify({"error": str(exc)}), 404

    @app.post("/api/files/directories")
    def create_directory():
        try:
            data = request.get_json(force=True); parent = data.get("parent", "")
            directory = file_path(parent, allow_root=True)
            if not directory.is_dir(): return jsonify({"error": "répertoire parent introuvable"}), 404
            target = child_path(parent, data.get("name"))
            target.mkdir()
            return jsonify({"created": target.relative_to(config.root.resolve()).as_posix()}), 201
        except FileExistsError:
            return jsonify({"error": "un élément porte déjà ce nom"}), 409
        except (OSError, TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/files")
    def create_file():
        try:
            data = request.get_json(force=True); parent = data.get("parent", "")
            directory = file_path(parent, allow_root=True)
            if not directory.is_dir(): return jsonify({"error": "répertoire parent introuvable"}), 404
            target = child_path(parent, data.get("name"))
            content = data.get("content", "")
            if not isinstance(content, str) or len(content.encode("utf-8")) > text_limit:
                return jsonify({"error": "contenu invalide ou supérieur à 1 Mio"}), 400
            with target.open("xb") as output: output.write(content.encode("utf-8"))
            return jsonify({"created": target.relative_to(config.root.resolve()).as_posix()}), 201
        except FileExistsError:
            return jsonify({"error": "un élément porte déjà ce nom"}), 409
        except (OSError, TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/files/upload")
    def upload_file():
        try:
            parent = request.form.get("parent", "")
            directory = file_path(parent, allow_root=True)
            if not directory.is_dir(): return jsonify({"error": "répertoire parent introuvable"}), 404
            uploads = request.files.getlist("files")
            if not uploads: return jsonify({"error": "aucun fichier transmis"}), 400
            created = []
            for upload in uploads:
                name = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
                target = child_path(parent, name)
                if target.exists() or target.is_symlink(): raise FileExistsError(name)
                write_atomic(target, upload.stream, 1024 * 1024 * 1024, overwrite=False)
                created.append(target.relative_to(config.root.resolve()).as_posix())
            return jsonify({"created": created}), 201
        except FileExistsError as exc:
            return jsonify({"error": f"un élément porte déjà ce nom : {exc}"}), 409
        except (OSError, TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/files/content")
    def read_file():
        try:
            target = file_path(request.args.get("path", ""))
            if not target.is_file(): return jsonify({"error": "fichier introuvable"}), 404
            if target.stat().st_size > text_limit: return jsonify({"error": "fichier supérieur à 1 Mio : utilisez Télécharger"}), 413
            try: content = target.read_text(encoding="utf-8")
            except UnicodeDecodeError: return jsonify({"error": "fichier binaire : utilisez Télécharger"}), 415
            return jsonify({"path": target.relative_to(config.root.resolve()).as_posix(), "content": content})
        except ValueError as exc: return jsonify({"error": str(exc)}), 400
        except OSError as exc: return jsonify({"error": str(exc)}), 404

    @app.put("/api/files/content")
    def update_file():
        try:
            data = request.get_json(force=True); target = file_path(data.get("path", ""))
            content = data.get("content")
            if not target.is_file(): return jsonify({"error": "fichier introuvable"}), 404
            if not isinstance(content, str) or len(content.encode("utf-8")) > text_limit:
                return jsonify({"error": "contenu invalide ou supérieur à 1 Mio"}), 400
            mode = stat.S_IMODE(target.stat().st_mode)
            write_atomic(target, io.BytesIO(content.encode("utf-8")), text_limit)
            target.chmod(mode)
            return jsonify({"saved": target.relative_to(config.root.resolve()).as_posix()})
        except (OSError, TypeError, ValueError) as exc: return jsonify({"error": str(exc)}), 400

    @app.get("/api/files/download")
    def download_file():
        try:
            target = file_path(request.args.get("path", ""))
            if not target.is_file(): return jsonify({"error": "fichier introuvable"}), 404
            return send_file(target, as_attachment=True, download_name=target.name)
        except ValueError as exc: return jsonify({"error": str(exc)}), 400
        except OSError as exc: return jsonify({"error": str(exc)}), 404

    @app.post("/api/files/rename")
    def rename_file():
        try:
            data = request.get_json(force=True)
            source = file_path(data.get("path", ""), allow_symlink=True)
            destination = file_path(data.get("destination", ""), allow_symlink=True)
            if not source.exists() and not source.is_symlink(): return jsonify({"error": "élément introuvable"}), 404
            if destination.exists() or destination.is_symlink(): return jsonify({"error": "la destination existe déjà"}), 409
            if not destination.parent.is_dir(): return jsonify({"error": "répertoire de destination introuvable"}), 404
            if source.is_dir() and not source.is_symlink() and destination.parent.is_relative_to(source):
                return jsonify({"error": "un répertoire ne peut pas être déplacé dans lui-même"}), 400
            source.rename(destination)
            return jsonify({"renamed": destination.relative_to(config.root.resolve()).as_posix()})
        except (OSError, TypeError, ValueError) as exc: return jsonify({"error": str(exc)}), 400

    @app.delete("/api/files")
    def delete_file():
        try:
            target = file_path(request.args.get("path", ""), allow_symlink=True)
            if target.is_symlink() or target.is_file(): target.unlink()
            elif target.is_dir(): shutil.rmtree(target)
            else: return jsonify({"error": "élément introuvable"}), 404
            return jsonify({"deleted": request.args.get("path")})
        except ValueError as exc: return jsonify({"error": str(exc)}), 400
        except OSError as exc: return jsonify({"error": str(exc)}), 400

    @app.post("/api/conflicts/<int:conflict_id>/restore")
    def restore_conflict(conflict_id):
        try: return jsonify(control(config,"restore-conflict",{"id":conflict_id}))
        except (OSError,RuntimeError,ValueError) as exc: return jsonify({"error":str(exc)}),400

    @app.post("/api/control/<action>")
    def actions(action):
        if action not in {"pause","resume","resync","reload"}: return jsonify({"error":"action invalide"}),404
        try: return jsonify(control(config,action))
        except (OSError,RuntimeError) as exc: return jsonify({"error":str(exc)}),503

    @app.errorhandler(OSError)
    def unavailable(exc): return jsonify({"error":"démon DFR indisponible","detail":str(exc)}),503
    @app.errorhandler(413)
    def too_large(exc): return jsonify({"error":"la requête dépasse la limite d’upload de 1 Gio"}),413
    return app
