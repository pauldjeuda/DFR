from io import BytesIO
from pathlib import Path

from dfr import web


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "dfr.conf"
    path.write_text(
        f"""[node]
id = node-a
root = {tmp_path / 'data'}
state_db = {tmp_path / 'state.db'}
[sync]
[peers]
[ssh]
key = {tmp_path / 'id_ed25519'}
[logging]
file = {tmp_path / 'dfr.log'}
[web]
bind = 127.0.0.1:8080
socket = {tmp_path / 'dfr.sock'}
""",
        encoding="utf-8",
    )
    return path


def test_dashboard_api_filters_logs_and_exports(tmp_path, monkeypatch):
    calls = []

    def fake_control(config, command, args=None):
        calls.append((command, args or {}))
        if command == "logs":
            return [{"ts": 1_800_000_000, "level": "info", "node": "node-a", "event": "sync",
                     "path": "a.txt", "peer": "node-b", "result": "ok", "duration_ms": 2,
                     "bytes": 4, "detail": "done"}]
        if command == "audit":
            return {"logged": True}
        if command == "conflicts":
            return []
        return {"node": "node-a", "running": True}

    monkeypatch.setattr(web, "control", fake_control)
    client = web.create_app(str(write_config(tmp_path))).test_client()

    response = client.get("/api/logs?from=100&to=200&node=node-a&type=sync&level=info&limit=5")
    assert response.status_code == 200, response.json
    assert response.json["items"][0]["path"] == "a.txt"
    logs_call = next(args for command, args in calls if command == "logs")
    assert logs_call["from"] == "100"
    assert logs_call["to"] == "200"
    assert logs_call["node"] == "node-a"
    assert logs_call["type"] == "sync"
    assert logs_call["limit"] == "5"

    response = client.get("/api/logs/export?format=csv")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "a.txt" in response.get_data(as_text=True)


def test_dashboard_includes_shared_files_and_peer_controls(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "control", lambda *args, **kwargs: {"logged": True})
    client = web.create_app(str(write_config(tmp_path))).test_client()
    response = client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="files-panel"' in page
    assert 'id="nodes-panel"' in page
    assert 'id="peer-form"' in page
    assert 'id="activity-panel"' in page
    assert 'id="uptime"' not in page


def test_dashboard_rejects_cross_site_mutating_request(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "control", lambda *args, **kwargs: {"paused": True})
    client = web.create_app(str(write_config(tmp_path))).test_client()

    response = client.post(
        "/api/control/pause",
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403
    response = client.put("/api/config/peers", json={"peers": []}, headers={
        "Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"
    })
    assert response.status_code == 403


def test_dashboard_restore_conflict_forwards_conflict_id(tmp_path, monkeypatch):
    calls = []

    def fake_control(config, command, args=None):
        calls.append((command, args or {}))
        if command == "audit":
            return {"logged": True}
        return {"restored": True}

    monkeypatch.setattr(web, "control", fake_control)
    client = web.create_app(str(write_config(tmp_path))).test_client()

    response = client.post("/api/conflicts/42/restore")

    assert response.status_code == 200
    assert response.json == {"restored": True}
    assert ("restore-conflict", {"id": 42}) in calls


def test_shared_files_api_supports_crud_and_upload(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()

    def fake_control(config, command, args=None):
        return {"logged": True}

    monkeypatch.setattr(web, "control", fake_control)
    client = web.create_app(str(write_config(tmp_path))).test_client()

    response = client.post("/api/files/directories", json={"parent": "", "name": "Documents"})
    assert response.status_code == 201
    assert response.json["created"] == "Documents"

    response = client.post("/api/files/upload", data={
        "parent": "Documents", "files": (BytesIO("bonjour".encode()), "été 2026.txt")
    }, content_type="multipart/form-data")
    assert response.status_code == 201
    assert response.json["created"] == ["Documents/été 2026.txt"]
    response = client.post("/api/files/upload", data={
        "parent": "Documents", "files": (BytesIO(b"autre"), "été 2026.txt")
    }, content_type="multipart/form-data")
    assert response.status_code == 409
    assert (root / "Documents" / "été 2026.txt").read_text(encoding="utf-8") == "bonjour"

    response = client.get("/api/files?path=Documents")
    assert response.status_code == 200
    assert response.json["items"][0]["name"] == "été 2026.txt"
    path = "Documents/été 2026.txt"
    response = client.get("/api/files/content", query_string={"path": path})
    assert response.json["content"] == "bonjour"
    response = client.get("/api/files/download", query_string={"path": path})
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith("attachment;")
    response.close()

    response = client.put("/api/files/content", json={"path": path, "content": "modifié"})
    assert response.status_code == 200, response.json
    assert (root / path).read_text(encoding="utf-8") == "modifié"

    response = client.post("/api/files/rename", json={"path": path, "destination": "Documents/nouveau.txt"})
    assert response.status_code == 200
    assert (root / "Documents/nouveau.txt").read_text(encoding="utf-8") == "modifié"

    response = client.post("/api/files/directories", json={"parent": "", "name": "Archive"})
    assert response.status_code == 201
    response = client.post("/api/files/rename", json={"path": "Documents/nouveau.txt", "destination": "Archive/nouveau.txt"})
    assert response.status_code == 200
    assert (root / "Archive/nouveau.txt").exists()

    response = client.delete("/api/files", query_string={"path": "Archive"})
    assert response.status_code == 200
    assert not (root / "Archive").exists()
    response = client.delete("/api/files", query_string={"path": ""})
    assert response.status_code == 400


def test_shared_files_api_rejects_traversal_and_symlink_escape(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    monkeypatch.setattr(web, "control", lambda *args, **kwargs: {"logged": True})
    client = web.create_app(str(write_config(tmp_path))).test_client()
    response = client.get("/api/files", query_string={"path": "../" + outside.name})
    assert response.status_code == 400

    try:
        (root / "escape").symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    response = client.get("/api/files/content", query_string={"path": "escape"})
    assert response.status_code == 400
    assert outside.read_text(encoding="utf-8") == "secret"


def test_peer_configuration_can_be_updated_and_rejects_invalid_entries(tmp_path, monkeypatch):
    config_path = write_config(tmp_path)
    calls = []

    def fake_control(config, command, args=None):
        calls.append(command)
        return {"queued": True}

    monkeypatch.setattr(web, "control", fake_control)
    client = web.create_app(str(config_path)).test_client()
    response = client.put("/api/config/peers", json={"peers": [{"id": "node-b", "target": "dfr@node-b"}]})
    assert response.status_code == 202
    assert response.json["peers"] == [{"id": "node-b", "target": "dfr@node-b"}]
    assert calls.count("reload") == 1
    assert client.get("/api/config/peers").json == [{"id": "node-b", "target": "dfr@node-b"}]

    response = client.put("/api/config/peers", json={"peers": [{"id": "node-a", "target": "dfr@node-a"}]})
    assert response.status_code == 400
    assert "propre pair" in response.json["error"]
