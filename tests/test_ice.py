import json
import secrets
import socket

import pytest

pytest.importorskip("aiortc", reason="ICE integration dependencies are installed via the [ice] extra")

from rhexora.client import Peer, RhexoraClient
from rhexora.ice import IceSignalBroker, run_ice_worker
from rhexora.identity import certificate_fingerprint, initialize_node
from rhexora.server import RhexoraServer


def free_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_real_ice_datachannel_tunnels_pinned_tls_to_worker(tmp_path, monkeypatch):
    node_dir = tmp_path / "worker"
    worker_port = free_tcp_port()
    config = initialize_node(node_dir, listen_host="127.0.0.1", listen_port=worker_port)
    config["discovery_enabled"] = False
    (node_dir / "node.json").write_text(json.dumps(config), encoding="utf-8")
    worker_server = RhexoraServer(node_dir)
    await worker_server.start()

    broker = IceSignalBroker()
    signaling_port = await broker.start("127.0.0.1", 0)
    room = secrets.token_urlsafe(32)
    worker_task = __import__("asyncio").create_task(
        run_ice_worker(
            str(node_dir), f"http://127.0.0.1:{signaling_port}", room,
            reconnect_seconds=0.2, stun_urls=[], local_host="127.0.0.1", local_port=worker_port,
        )
    )
    try:
        peer = Peer(
            name="ice-worker", host="127.0.0.1", port=worker_port,
            tls_fingerprint=certificate_fingerprint(node_dir / config["tls_cert"]),
            token=config["auth_token"], node_id=config["node_id"],
            ice_signal_url=f"http://127.0.0.1:{signaling_port}", ice_room=room,
        )
        client = RhexoraClient(timeout=40)
        async def forbid_direct_fallback(_peer, _request):
            raise AssertionError("ICE integration test must not pass through direct TCP fallback")
        monkeypatch.setattr(client, "_exchange_direct", forbid_direct_fallback)
        result = await client.execute(peer, "math.count_primes", {"start": 1, "stop": 1000})
        assert result["result"]["count"] == 168
        assert result["node_id"] == config["node_id"]
    finally:
        worker_task.cancel()
        await __import__("asyncio").gather(worker_task, return_exceptions=True)
        await broker.close()
        await worker_server.close()
