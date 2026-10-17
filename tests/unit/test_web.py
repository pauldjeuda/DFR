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
    assert response.status_code == 200
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


def test_dashboard_rejects_cross_site_mutating_request(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "control", lambda *args, **kwargs: {"paused": True})
    client = web.create_app(str(write_config(tmp_path))).test_client()

    response = client.post(
        "/api/control/pause",
        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
    )

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
