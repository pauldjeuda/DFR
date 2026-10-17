import pytest

from dfr.cli import safe_path
from dfr.config import Config


def test_safe_path_rejects_parent_escape(tmp_path):
    config = Config(node_id="n1", root=tmp_path / "data")
    with pytest.raises(ValueError, match="invalide"):
        safe_path(config, "../outside")


def test_safe_path_rejects_symlink_parent_escape(tmp_path):
    config = Config(node_id="n1", root=tmp_path / "data")
    config.root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    try:
        (config.root / "jump").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"création de lien symbolique non autorisée sur cet hôte: {exc}")
    with pytest.raises(ValueError, match="hors racine"):
        safe_path(config, "jump/secret")


def test_safe_path_reserves_internal_metadata_directory(tmp_path):
    config = Config(node_id="n1", root=tmp_path / "data")
    with pytest.raises(ValueError, match="réservé"):
        safe_path(config, ".dfr/state.db")


def test_safe_path_allows_internal_path_only_for_restore(tmp_path):
    config = Config(node_id="n1", root=tmp_path / "data")
    assert safe_path(config, ".dfr/conflicts/saved", allow_internal=True) == (
        config.root / ".dfr/conflicts/saved"
    )
