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


def test_reject_invalid_node_and_peer_ids(tmp_path):
    path = write_config(tmp_path).read_text(encoding="utf-8")
    bad_node = tmp_path / "bad-node.conf"
    bad_node.write_text(path.replace("id = node-a", "id = node/a"), encoding="utf-8")
    with pytest.raises(ValueError, match="identifiant simple"):
        Config.load(bad_node)

    bad_peer = tmp_path / "bad-peer.conf"
    bad_peer.write_text(path.replace("[peers]\n", "[peers]\nnot a peer = dfr@host\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="identifiant de pair invalide"):
        Config.load(bad_peer)


def test_reject_more_than_four_peers(tmp_path):
    peers = "".join(f"node-{letter} = dfr@node-{letter}\n" for letter in "bcdef")
    with pytest.raises(ValueError, match="maximum 4 pairs"):
        Config.load(write_config(tmp_path, peers))


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("bind", "0.0.0.0:0", "web.bind"),
        ("level", "verbose", "level doit"),
        ("retention_days", "0", "retention_days"),
    ],
)
def test_reject_invalid_web_and_log_settings(tmp_path, setting, value, message):
    text = write_config(tmp_path).read_text(encoding="utf-8")
    section, key = ("web", "bind") if setting == "bind" else ("logging", setting)
    marker = f"[{section}]\n"
    path = tmp_path / f"bad-{setting}.conf"
    if key == "bind":
        text = text.replace("bind = 127.0.0.1:8080", f"bind = {value}")
    elif key == "level":
        text = text.replace("level = info", f"level = {value}")
    else:
        text = text.replace(marker, f"{marker}{key} = {value}\n")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        Config.load(path)


def test_load_config_keeps_exclusions_and_paths_absolute(tmp_path):
    text = write_config(tmp_path).read_text(encoding="utf-8")
    text = text.replace("debounce_ms = 1000", "debounce_ms = 250\nexclude = *.bak, cache.*")
    path = tmp_path / "custom.conf"
    path.write_text(text, encoding="utf-8")
    config = Config.load(path)
    assert config.debounce_ms == 250
    assert config.excludes == ("*.bak", "cache.*")
    assert all(item.is_absolute() for item in (config.root, config.state_db, config.log_file, config.socket))
