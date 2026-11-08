import asyncio
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rhexora.client import Peer, RhexoraClient
from rhexora.identity import certificate_fingerprint, initialize_node
from rhexora.server import RhexoraServer


@pytest.mark.asyncio
async def test_authenticated_media_upload_transcode_and_download(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are not installed")
    from rhexora.video import video_capabilities
    if not video_capabilities()["available"]:
        pytest.skip("libx264 is not available")

    node_dir = tmp_path / "worker"
    config = initialize_node(node_dir, listen_host="127.0.0.1", listen_port=0, enable_video=True)
    config["discovery_enabled"] = False
    (node_dir / "node.json").write_text(json.dumps(config), encoding="utf-8")
    server = RhexoraServer(node_dir)
    port = await server.start()
    peer = Peer(
        name="media-worker", host="127.0.0.1", port=port,
        tls_fingerprint=certificate_fingerprint(node_dir / config["tls_cert"]),
        token=config["auth_token"], node_id=config["node_id"],
    )

    source = tmp_path / "source.mp4"
    generated = subprocess.run([
        shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=10", "-t", "2",
        "-g", "10", "-pix_fmt", "yuv420p", str(source),
    ], capture_output=True, text=True, timeout=30)
    assert generated.returncode == 0, generated.stderr
    client = RhexoraClient(timeout=30)
    try:
        uploaded = await client.upload_media(peer, source)
        assert uploaded["input_file"].startswith("uploads/")
        result = await client.execute(peer, "media.video.transcode", {
            "input_file": uploaded["input_file"], "encoder": "libx264", "allow_cpu_fallback": False,
        })
        metadata = result["result"]
        downloaded = await client.download_media(
            peer, metadata["output_file"], tmp_path / "downloaded.mp4",
            expected_size=metadata["output_bytes"], expected_sha256=metadata["output_sha256"],
        )
        assert downloaded.is_file()
        assert hashlib.sha256(downloaded.read_bytes()).hexdigest() == metadata["output_sha256"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_media_upload_requires_authentication_and_contiguous_chunks(tmp_path: Path):
    import base64
    import uuid
    from dataclasses import replace
    from rhexora.errors import RemoteTaskError

    node_dir = tmp_path / "worker"
    config = initialize_node(node_dir, listen_host="127.0.0.1", listen_port=0, enable_video=True)
    config["discovery_enabled"] = False
    (node_dir / "node.json").write_text(json.dumps(config), encoding="utf-8")
    server = RhexoraServer(node_dir)
    port = await server.start()
    peer = Peer(
        name="media-worker", host="127.0.0.1", port=port,
        tls_fingerprint=certificate_fingerprint(node_dir / config["tls_cert"]),
        token=config["auth_token"], node_id=config["node_id"],
    )
    client = RhexoraClient(timeout=5)
    upload_id = uuid.uuid4().hex
    try:
        bad = replace(peer, token="X" * 40)
        with pytest.raises(RemoteTaskError, match="Authentication failed"):
            await client._media_request(bad, {
                "operation": "start", "upload_id": upload_id, "size": 3,
                "sha256": hashlib.sha256(b"abc").hexdigest(),
            })
        await client._media_request(peer, {
            "operation": "start", "upload_id": upload_id, "size": 3,
            "sha256": hashlib.sha256(b"abc").hexdigest(),
        })
        with pytest.raises(RemoteTaskError, match="sequence"):
            await client._media_request(peer, {
                "operation": "chunk", "upload_id": upload_id, "sequence": 1,
                "data": base64.b64encode(b"abc").decode("ascii"),
            })
        await client._media_request(peer, {"operation": "abort", "upload_id": upload_id})
    finally:
        await server.close()
