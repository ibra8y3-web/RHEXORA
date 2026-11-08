import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from rhexora.client import RhexoraClient, Peer
from rhexora.errors import RemoteTaskError, SecurityError
from rhexora.identity import certificate_fingerprint, initialize_node
from rhexora.server import RhexoraServer


async def _make_node(path: Path):
    config = initialize_node(path, listen_host="127.0.0.1", listen_port=0, max_items=100)
    config["discovery_enabled"] = False
    (path / "node.json").write_text(__import__("json").dumps(config), encoding="utf-8")
    server = RhexoraServer(path)
    port = await server.start()
    peer = Peer(
        name=config["node_id"],
        host="127.0.0.1",
        port=port,
        tls_fingerprint=certificate_fingerprint(path / config["tls_cert"]),
        token=config["auth_token"],
        node_id=config["node_id"],
        max_items=config["max_items"],
    )
    return server, peer


@pytest.mark.asyncio
async def test_real_tls_request_to_two_worker_nodes(tmp_path: Path):
    server_a, peer_a = await _make_node(tmp_path / "a")
    server_b, peer_b = await _make_node(tmp_path / "b")
    try:
        client = RhexoraClient(timeout=5)
        direct = await client.execute(peer_a, "math.sum_ints", {"values": [1, 2, 3]})
        assert direct["result"] == {"sum": 6, "count": 3}

        first_attempt = await client.execute(
            peer_a, "math.sum_ints", {"values": [7, 8, 9]}, request_id="stable-task-001"
        )
        repeated_attempt = await client.execute(
            peer_a, "math.sum_ints", {"values": [7, 8, 9]}, request_id="stable-task-001"
        )
        assert first_attempt["result"] == repeated_attempt["result"] == {"sum": 24, "count": 3}
        assert first_attempt["request_id"] == repeated_attempt["request_id"] == "stable-task-001"
        with pytest.raises(RemoteTaskError, match="already used"):
            await client.execute(
                peer_a, "math.sum_ints", {"values": [1, 1]}, request_id="stable-task-001"
            )

        text_result = await client.execute(peer_b, "text.analyze", {"text": "one two\nthree"})
        assert text_result["result"] == {"characters": 13, "words": 3, "lines": 2}

        distributed = await client.distributed_sum(
            list(range(1, 11)), [peer_a, peer_b], chunk_size=2
        )
        assert distributed["sum"] == 55
        assert distributed["count"] == 10
        assert distributed["chunks_completed"] == 5
        assert set(distributed["workers"]) == {peer_a.node_id, peer_b.node_id}

        prime_result = await client.distributed_prime_count(
            0, 100, [peer_a, peer_b], chunk_size=25
        )
        assert prime_result["prime_count"] == 25
        assert prime_result["chunks_completed"] == 4
        assert set(prime_result["workers"]) == {peer_a.node_id, peer_b.node_id}
    finally:
        await server_a.close()
        await server_b.close()


@pytest.mark.asyncio
async def test_bad_tls_fingerprint_is_rejected(tmp_path: Path):
    server, peer = await _make_node(tmp_path / "node")
    try:
        bad_peer = Peer(
            name=peer.name,
            host=peer.host,
            port=peer.port,
            tls_fingerprint="0" * 64,
            token=peer.token,
            node_id=peer.node_id,
        )
        with pytest.raises(SecurityError, match="fingerprint mismatch"):
            await RhexoraClient(timeout=5).execute(bad_peer, "math.sum_ints", {"values": [2]})
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_bad_token_is_rejected(tmp_path: Path):
    server, peer = await _make_node(tmp_path / "node")
    try:
        bad_peer = Peer(
            name=peer.name,
            host=peer.host,
            port=peer.port,
            tls_fingerprint=peer.tls_fingerprint,
            token="X" * 40,
            node_id=peer.node_id,
        )
        with pytest.raises(RemoteTaskError, match="Authentication failed"):
            await RhexoraClient(timeout=5).execute(bad_peer, "math.sum_ints", {"values": [2]})
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_worker_refuses_non_allowlisted_task(tmp_path: Path):
    server, peer = await _make_node(tmp_path / "node")
    try:
        with pytest.raises(RemoteTaskError, match="capability is disabled"):
            await RhexoraClient(timeout=5).execute(peer, "data.delete", {"path": "/"})
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_completed_task_is_cached_across_worker_restart(tmp_path: Path):
    data_dir = tmp_path / "persistent-worker"
    server, peer = await _make_node(data_dir)
    client = RhexoraClient(timeout=5)
    try:
        first = await client.execute(
            peer, "math.sum_ints", {"values": [100, 200, 300]}, request_id="persisted-job-01"
        )
    finally:
        await server.close()

    restarted_server = RhexoraServer(data_dir)
    restarted_port = await restarted_server.start()
    # The test node was initialized on port 0, so the OS assigns a new port after restart.
    restarted_peer = replace(peer, port=restarted_port)
    try:
        second = await client.execute(
            restarted_peer, "math.sum_ints", {"values": [100, 200, 300]}, request_id="persisted-job-01"
        )
        assert second["result"] == first["result"] == {"sum": 600, "count": 3}
        assert second["request_id"] == first["request_id"]
    finally:
        await restarted_server.close()

@pytest.mark.asyncio
async def test_simultaneous_duplicate_request_is_executed_once(tmp_path: Path, monkeypatch):
    import threading
    import time

    import rhexora.server as server_module

    server, peer = await _make_node(tmp_path / "concurrent-worker")
    calls = 0
    lock = threading.Lock()
    original_execute_task = server_module.execute_task

    def counted_execute_task(*args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.12)
        return original_execute_task(*args, **kwargs)

    monkeypatch.setattr(server_module, "execute_task", counted_execute_task)
    try:
        client = RhexoraClient(timeout=5)
        first, second = await asyncio.gather(
            client.execute(
                peer, "math.sum_ints", {"values": [4, 5, 6]}, request_id="concurrent-job-01"
            ),
            client.execute(
                peer, "math.sum_ints", {"values": [4, 5, 6]}, request_id="concurrent-job-01"
            ),
        )
        assert first["result"] == second["result"] == {"sum": 15, "count": 3}
        assert calls == 1
    finally:
        await server.close()

@pytest.mark.asyncio
async def test_video_task_runs_over_authenticated_tls_when_explicitly_enabled(tmp_path: Path):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are not installed")
    from rhexora.video import video_capabilities
    if not video_capabilities()["available"]:
        pytest.skip("libx264 is not available")

    data_dir = tmp_path / "video-worker"
    config = initialize_node(data_dir, listen_host="127.0.0.1", listen_port=0, enable_video=True)
    config["discovery_enabled"] = False
    (data_dir / "node.json").write_text(__import__("json").dumps(config), encoding="utf-8")
    media_dir = data_dir / "shared-media"
    media_dir.mkdir(exist_ok=True)
    source = media_dir / "sample.mp4"
    generated = subprocess.run([
        shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=128x72:rate=8", "-t", "1", "-pix_fmt", "yuv420p", str(source)
    ], capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stderr

    server = RhexoraServer(data_dir)
    port = await server.start()
    peer = Peer(
        name="video-worker", host="127.0.0.1", port=port,
        tls_fingerprint=certificate_fingerprint(data_dir / config["tls_cert"]),
        token=config["auth_token"], node_id=config["node_id"], max_items=config["max_items"],
    )
    try:
        response = await RhexoraClient(timeout=15).execute(
            peer, "media.video.transcode", {"input_file": "sample.mp4", "encoder": "libx264"}
        )
        output_path = data_dir / response["result"]["output_relative_path"]
        assert output_path.is_file() and output_path.stat().st_size > 0
        assert response["result"]["output"]["video_codec"] == "h264"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_ice_relay_direct_fallback_order(monkeypatch):
    base = Peer(
        name="worker", host="worker.example.net", port=47821,
        tls_fingerprint="a" * 64, token="b" * 32, node_id="node-1",
    )
    peer = replace(
        base, ice_signal_url="https://signal.example.net", ice_room="c" * 43,
        relay_host="relay.example.net", relay_port=47825, relay_token="d" * 32,
    )
    client = RhexoraClient(timeout=1)
    calls: list[str] = []

    async def ice(_peer, _request):
        calls.append("ice")
        raise RemoteTaskError("ICE unavailable")

    def relay(_peer, _request):
        calls.append("relay")
        raise RemoteTaskError("Relay unavailable")

    async def direct(_peer, _request):
        calls.append("direct")
        return {"kind": "result"}

    monkeypatch.setattr(client, "_exchange_via_ice", ice)
    monkeypatch.setattr(client, "_exchange_via_relay", relay)
    monkeypatch.setattr(client, "_exchange_direct", direct)
    result = await client._exchange(peer, {"kind": "execute"})
    assert result == {"kind": "result"}
    assert calls == ["ice", "relay", "direct"]


@pytest.mark.asyncio
async def test_tls_identity_failure_never_downgrades_to_other_transport(monkeypatch):
    base = Peer(
        name="worker", host="worker.example.net", port=47821,
        tls_fingerprint="a" * 64, token="b" * 32, node_id="node-1",
    )
    peer = replace(base, ice_signal_url="https://signal.example.net", ice_room="c" * 43)
    client = RhexoraClient(timeout=1)
    relay_called = False

    async def ice(_peer, _request):
        raise SecurityError("TLS fingerprint mismatch")

    def relay(_peer, _request):
        nonlocal relay_called
        relay_called = True
        return {"kind": "result"}

    monkeypatch.setattr(client, "_exchange_via_ice", ice)
    monkeypatch.setattr(client, "_exchange_via_relay", relay)
    with pytest.raises(SecurityError, match="fingerprint"):
        await client._exchange(peer, {"kind": "execute"})
    assert not relay_called
