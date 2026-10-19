from dfr.daemon import DFRDaemon


def test_peer_status_reports_fresh_remote_identity_and_failure():
    daemon = object.__new__(DFRDaemon)
    daemon.state = type("StateStub", (), {"peers_status": lambda self: [
        {"id": "node-b", "online": 0, "clock_delta": 0},
        {"id": "node-c", "online": 1, "clock_delta": 0.1},
    ]})()

    class TransportStub:
        @staticmethod
        def peer_status(peer_id):
            if peer_id == "node-c":
                raise RuntimeError("connexion refusée")
            return {"node": "node-b", "server_time_ns": 1_000_000_000, "exists": True}

    daemon.transport = TransportStub()
    result = {peer["id"]: peer for peer in daemon.peer_status()}

    assert result["node-b"]["reachable"] is True
    assert result["node-b"]["reported_node"] == "node-b"
    assert result["node-b"]["identity_matches"] is True
    assert result["node-b"]["root_available"] is True
    assert result["node-b"]["checked_at"] > 0
    assert result["node-c"]["reachable"] is False
    assert result["node-c"]["probe_error"] == "connexion refusée"
    assert result["node-c"]["reported_node"] is None
