from pathlib import Path

import pytest

from dfr.config import Config


def write_config(tmp_path: Path, peers="") -> Path:
    path = tmp_path / "dfr.conf"
    path.write_text(f"""[node]
id = node-a
root = {tmp_path / 'data'}
state_db = {tmp_path / 'state.db'}
[sync]
debounce_ms = 1000
reconcile_interval_s = 300
[peers]
{peers}[ssh]
key = {tmp_path / 'key'}
[logging]
level = info
file = {tmp_path / 'dfr.log'}
[web]
bind = 127.0.0.1:8080
socket = {tmp_path / 'dfr.sock'}
""", encoding="utf-8")
    return path


def test_load_config_defaults_and_peer(tmp_path):
    config = Config.load(write_config(tmp_path, "node-b = dfr@192.0.2.2\n"))
    assert config.node_id == "node-a"
    assert config.root == tmp_path / "data"
    assert config.peers == {"node-b": "dfr@192.0.2.2"}
    assert config.reconcile_interval_s == 300


def test_reject_relative_root(tmp_path):
    config = write_config(tmp_path).read_text(encoding="utf-8").replace(str(tmp_path / "data"), "relative")
    path = tmp_path / "bad.conf"; path.write_text(config, encoding="utf-8")
    with pytest.raises(ValueError, match="absolu"):
        Config.load(path)


def test_reject_self_peer(tmp_path):
    with pytest.raises(ValueError, match="propre pair"):
        Config.load(write_config(tmp_path, "node-a = dfr@127.0.0.1\n"))
