"""Local Flask dashboard and REST API."""
from __future__ import annotations

import csv
import io
import json
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request

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
    import os
    config=Config.load(os.environ.get("DFR_CONFIG", config_path)); app.config["DFR_CONFIG"]=config

    @app.before_request
    def protect_local_actions():
        if request.method != "POST":
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
    return app
