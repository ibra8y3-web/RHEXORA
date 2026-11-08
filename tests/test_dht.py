import asyncio
import socket
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from rhexora.dht import DHTNode, DHTStore, RoutingTable, make_record, verify_record, xor_distance


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_signed_peer_record_verifies_and_detects_tampering():
    key = Ed25519PrivateKey.generate()
    record = make_record(key, "127.0.0.1", 47823)
    assert verify_record(record)
    changed = dict(record, host="attacker.invalid")
    assert not verify_record(changed)


def test_routing_table_orders_nodes_by_xor_distance():
    local = "0" * 40
    table = RoutingTable(local)
    records = [make_record(Ed25519PrivateKey.generate(), "127.0.0.1", 4000 + i) for i in range(4)]
    for record in records:
        assert table.add(record)
    expected = sorted(records, key=lambda item: xor_distance(item["node_id"], local))
    assert [r["node_id"] for r in table.closest(local, 4)] == [r["node_id"] for r in expected]


def test_dht_store_rejects_invalid_and_stale_records(tmp_path: Path):
    store = DHTStore(tmp_path / "dht.sqlite3")
    key = Ed25519PrivateKey.generate()
    record = make_record(key, "127.0.0.1", 47823)
    assert store.put(record)
    assert store.get(record["node_id"]) == record
    assert not store.put(dict(record, host="evil.example"))


@pytest.mark.asyncio
async def test_three_dht_nodes_bootstrap_and_lookup(tmp_path: Path):
    a = DHTNode(tmp_path / "a", "127.0.0.1", port=free_udp_port(), bind_host="127.0.0.1", request_timeout=0.5, allow_private_endpoints=True)
    b = DHTNode(tmp_path / "b", "127.0.0.1", port=free_udp_port(), bind_host="127.0.0.1", request_timeout=0.5, allow_private_endpoints=True)
    c = DHTNode(tmp_path / "c", "127.0.0.1", port=free_udp_port(), bind_host="127.0.0.1", request_timeout=0.5, allow_private_endpoints=True)
    try:
        await a.start(); await b.start(); await c.start()
        assert await b.bootstrap([("127.0.0.1", a.bind_port)]) == 1
        assert await c.bootstrap([("127.0.0.1", b.bind_port)]) == 1
        found = await c.lookup(a.local_id)
        assert any(record["node_id"] == a.local_id for record in found)
        assert any(record["node_id"] == c.local_id for record in b.store.all())
    finally:
        await c.close(); await b.close(); await a.close()


@pytest.mark.asyncio
async def test_dht_refuses_private_endpoints_by_default(tmp_path: Path):
    node = DHTNode(tmp_path / "secure", "198.51.100.4", port=47823, bind_host="127.0.0.1")
    assert not await node._endpoint_is_routable("127.0.0.1", 47823)
    assert not await node._endpoint_is_routable("localhost", 47823)
    await node.close()


def test_routing_table_rejects_same_sequence_endpoint_rewrite():
    key = Ed25519PrivateKey.generate()
    first = make_record(key, "127.0.0.1", 47823, seq=7)
    changed_same_sequence = make_record(key, "127.0.0.2", 47824, seq=7)
    table = RoutingTable("0" * 40)
    assert table.add(first)
    assert not table.add(changed_same_sequence)


@pytest.mark.asyncio
async def test_sixteen_node_local_overlay_bootstraps_and_finds_seed(tmp_path: Path):
    nodes = [
        DHTNode(
            tmp_path / f"node-{index}", "127.0.0.1", port=free_udp_port(),
            bind_host="127.0.0.1", request_timeout=0.4, allow_private_endpoints=True,
        )
        for index in range(16)
    ]
    try:
        await asyncio.gather(*(node.start() for node in nodes))
        for index in range(1, len(nodes)):
            assert await nodes[index].bootstrap([("127.0.0.1", nodes[0].bind_port)]) == 1
            assert any(record["node_id"] == nodes[0].local_id for record in nodes[index].store.all())
        # Sixteen independent UDP sockets, Ed25519 identities and SQLite stores on a private overlay.
        found = await nodes[-1].lookup(nodes[0].local_id, max_queries=24)
        assert any(record["node_id"] == nodes[0].local_id for record in found)
        assert all(any(record["node_id"] == node.local_id for record in nodes[0].store.all()) for node in nodes[1:])
    finally:
        await asyncio.gather(*(node.close() for node in nodes))

@pytest.mark.asyncio
async def test_health_probe_does_not_add_probe_record_to_routing_table(tmp_path: Path):
    server = DHTNode(tmp_path / "server", "127.0.0.1", port=free_udp_port(), bind_host="127.0.0.1", allow_private_endpoints=True)
    client = DHTNode(tmp_path / "client", "127.0.0.1", port=free_udp_port(), bind_host="127.0.0.1", allow_private_endpoints=True)
    try:
        await server.start()
        await client.start()
        response = await client._send_request(("127.0.0.1", server.bind_port), "health")
        assert response["type"] == "healthy"
        assert server.store.get(client.local_id) is None
    finally:
        await client.close()
        await server.close()


@pytest.mark.asyncio
async def test_dht_rejects_validly_signed_record_with_unrelated_source_ip(tmp_path: Path):
    server = DHTNode(tmp_path / "source-check", "127.0.0.1", port=free_udp_port(), bind_host="127.0.0.1", allow_private_endpoints=True)
    await server.start()
    try:
        remote_key = Ed25519PrivateKey.generate()
        unrelated = make_record(remote_key, "203.0.113.77", 47823)
        packet = {"v": 1, "id": "source-check-1", "type": "ping", "sender": unrelated}
        await server._handle_packet(packet, ("127.0.0.1", 50000))
        assert server.store.get(unrelated["node_id"]) is None
        assert not any(item["node_id"] == unrelated["node_id"] for item in server.table.closest(unrelated["node_id"]))
    finally:
        await server.close()


def test_dht_source_rate_limiter_has_bounded_burst(tmp_path: Path):
    node = DHTNode(tmp_path / "rate-limit", "127.0.0.1", port=47823, bind_host="127.0.0.1")
    original = node.allow_inbound_datagram
    # Exercise the real token bucket with a deliberately tiny bucket for deterministic testing.
    import rhexora.dht as dht_module
    old_burst = dht_module.DHT_SOURCE_BURST
    try:
        dht_module.DHT_SOURCE_BURST = 2.0
        assert original(("192.0.2.10", 40000))
        assert original(("192.0.2.10", 40000))
        assert not original(("192.0.2.10", 40000))
        assert original(("192.0.2.11", 40000))
    finally:
        dht_module.DHT_SOURCE_BURST = old_burst


@pytest.mark.asyncio
async def test_dht_health_can_return_signed_neighbors_without_mutating_routing_table(tmp_path: Path):
    server = DHTNode(tmp_path / "read-only-server", "127.0.0.1", port=free_udp_port(), bind_host="127.0.0.1", allow_private_endpoints=True)
    client = DHTNode(tmp_path / "read-only-client", "127.0.0.1", port=free_udp_port(), bind_host="127.0.0.1", allow_private_endpoints=True)
    neighbor_key = Ed25519PrivateKey.generate()
    neighbor = make_record(neighbor_key, "127.0.0.1", 49001)
    try:
        await server.start()
        await client.start()
        assert server.table.add(neighbor)
        response = await client._send_request(("127.0.0.1", server.bind_port), "health", target=neighbor["node_id"])
        assert response["type"] == "healthy"
        assert any(record["node_id"] == neighbor["node_id"] for record in response["records"])
        assert server.store.get(client.local_id) is None
    finally:
        await client.close()
        await server.close()
