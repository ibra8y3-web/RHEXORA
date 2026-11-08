import os
from pathlib import Path

import pytest

from rhexora.errors import ConfigurationError
from rhexora.identity import certificate_fingerprint, initialize_node, load_node_config


def test_initialize_node_creates_private_credentials(tmp_path: Path):
    config = initialize_node(tmp_path / "node", listen_port=0)
    root, loaded = load_node_config(tmp_path / "node")
    assert loaded["node_id"] == config["node_id"]
    assert len(certificate_fingerprint(root / loaded["tls_cert"])) == 64
    assert (root / loaded["tls_key"]).stat().st_size > 0
    if os.name == "posix":
        assert (root / "node.json").stat().st_mode & 0o077 == 0


def test_initialize_refuses_to_overwrite_existing_node(tmp_path: Path):
    initialize_node(tmp_path / "node")
    with pytest.raises(ConfigurationError, match="already initialized"):
        initialize_node(tmp_path / "node")
