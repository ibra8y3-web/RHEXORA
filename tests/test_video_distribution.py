import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rhexora.client import Peer
from rhexora.identity import certificate_fingerprint, initialize_node
from rhexora.server import RhexoraServer
from rhexora.video_distribution import distributed_video_transcode


@pytest.mark.asyncio
async def test_distributed_video_is_uploaded_encoded_downloaded_and_assembled(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools are not installed")
    from rhexora.video import video_capabilities
    if not video_capabilities()["available"]:
        pytest.skip("libx264 is not available")

    servers = []
    peers = []
    for index in range(2):
        node_dir = tmp_path / f"worker-{index}"
        config = initialize_node(node_dir, listen_host="127.0.0.1", listen_port=0, enable_video=True)
        config["discovery_enabled"] = False
        (node_dir / "node.json").write_text(json.dumps(config), encoding="utf-8")
        server = RhexoraServer(node_dir)
        port = await server.start()
        servers.append(server)
        peers.append(Peer(
            name=f"worker-{index}", host="127.0.0.1", port=port,
            tls_fingerprint=certificate_fingerprint(node_dir / config["tls_cert"]),
            token=config["auth_token"], node_id=config["node_id"],
        ))

    source = tmp_path / "input.mp4"
    generated = subprocess.run([
        shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-t", "4",
        "-g", "10", "-keyint_min", "10", "-sc_threshold", "0", "-pix_fmt", "yuv420p", str(source),
    ], capture_output=True, text=True, timeout=45)
    assert generated.returncode == 0, generated.stderr
    output = tmp_path / "assembled.mp4"
    try:
        result = await distributed_video_transcode(
            source, output, peers, segment_seconds=2, encoder="libx264", timeout=120,
        )
        assert result["ok"] is True
        assert result["segments_completed"] >= 2
        assert len(result["workers"]) == 2
        assert output.is_file() and output.stat().st_size > 0
        assert result["output_video"]["codec_name"] == "h264"
    finally:
        for server in servers:
            await server.close()
