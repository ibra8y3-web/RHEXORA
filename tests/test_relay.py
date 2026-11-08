import asyncio
import json
from pathlib import Path

import pytest

from rhexora.client import Peer, RhexoraClient
from rhexora.identity import certificate_fingerprint, initialize_node
from rhexora.relay import RhexoraRelayServer, make_proof, run_relay_worker
from rhexora.server import RhexoraServer


@pytest.mark.asyncio
async def test_end_to_end_tls_task_through_opaque_relay(tmp_path: Path):
    data_dir = tmp_path / "worker"
    config = initialize_node(data_dir, listen_host="127.0.0.1", listen_port=0)
    config["discovery_enabled"] = False
    (data_dir / "node.json").write_text(json.dumps(config), encoding="utf-8")
    worker = RhexoraServer(data_dir)
    local_port = await worker.start()

    relay = RhexoraRelayServer({config["node_id"]: config["relay_token"]}, worker_wait_timeout=3)
    relay_port = await relay.start("127.0.0.1", 0)
    connector = asyncio.create_task(
        run_relay_worker(data_dir, "127.0.0.1", relay_port, local_host="127.0.0.1", local_port=local_port, reconnect_seconds=0.05)
    )
    try:
        for _ in range(100):
            if relay.workers[config["node_id"]].qsize() > 0:
                break
            await asyncio.sleep(0.01)
        assert relay.workers[config["node_id"]].qsize() == 1

        peer = Peer(
            name="relayed-worker",
            host="127.0.0.1",  # direct endpoint is not used when relay_host is set
            port=local_port,
            tls_fingerprint=certificate_fingerprint(data_dir / config["tls_cert"]),
            token=config["auth_token"],
            node_id=config["node_id"],
            relay_host="127.0.0.1",
            relay_port=relay_port,
            relay_token=config["relay_token"],
        )
        result = await RhexoraClient(timeout=5).execute(
            peer, "math.count_primes", {"start": 0, "stop": 100}
        )
        assert result["result"] == {"count": 25, "start": 0, "stop": 100}
    finally:
        connector.cancel()
        await asyncio.gather(connector, return_exceptions=True)
        await relay.close()
        await worker.close()


def test_relay_proof_is_bound_to_action_node_and_nonce():
    token = "test-token-that-is-long-enough-to-test"
    proof = make_proof(token, "worker", "node-123", "fresh-nonce")
    assert proof == make_proof(token, "worker", "node-123", "fresh-nonce")
    assert proof != make_proof(token, "connect", "node-123", "fresh-nonce")
    assert proof != make_proof(token, "worker", "node-other", "fresh-nonce")
    assert proof != make_proof(token, "worker", "node-123", "different-nonce")


def test_relay_credentials_command_and_private_relay_invite(tmp_path: Path, capsys):
    import os
    from rhexora.cli import main
    from rhexora.client import load_invite

    node_dir = tmp_path / "node"
    credentials_file = tmp_path / "relay-credentials.json"
    invite_file = tmp_path / "with-relay.invite.json"
    assert main(["init", "--data-dir", str(node_dir), "--host", "127.0.0.1", "--port", "47861"]) == 0
    config = json.loads((node_dir / "node.json").read_text(encoding="utf-8"))
    assert main(["relay-credentials", "--output", str(credentials_file), "--node-dir", str(node_dir)]) == 0
    saved = json.loads(credentials_file.read_text(encoding="utf-8"))
    assert saved == {config["node_id"]: config["relay_token"]}
    if os.name == "posix":
        assert credentials_file.stat().st_mode & 0o077 == 0

    assert main([
        "invite", "--data-dir", str(node_dir), "--host", "127.0.0.1", "--name", "worker",
        "--relay-host", "relay.example", "--relay-port", "47825", "--output", str(invite_file),
    ]) == 0
    peer = load_invite(invite_file)
    assert peer.relay_host == "relay.example"
    assert peer.relay_port == 47825
    assert peer.relay_token == config["relay_token"]
