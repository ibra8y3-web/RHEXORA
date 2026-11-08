import json
from pathlib import Path

from rhexora.cli import main


def test_cli_init_invite_and_peer_add(tmp_path: Path, capsys):
    worker_dir = tmp_path / "worker"
    requester_dir = tmp_path / "requester"
    invite = tmp_path / "worker.invite.json"

    assert main(["init", "--data-dir", str(worker_dir), "--port", "47891"]) == 0
    assert main([
        "invite", "--data-dir", str(worker_dir), "--host", "127.0.0.1",
        "--name", "worker-one", "--output", str(invite)
    ]) == 0
    assert main(["init", "--data-dir", str(requester_dir), "--port", "47892"]) == 0
    assert main(["peer", "add", str(invite), "--data-dir", str(requester_dir)]) == 0
    assert main(["peer", "list", "--data-dir", str(requester_dir)]) == 0

    peers = json.loads((requester_dir / "peers.json").read_text(encoding="utf-8"))
    assert len(peers) == 1
    assert peers[0]["name"] == "worker-one"
    assert peers[0]["host"] == "127.0.0.1"
    assert "Trusted peer added" in capsys.readouterr().out


def test_cli_ice_invite_preserves_room_capability(tmp_path: Path):
    from rhexora.client import load_invite

    worker_dir = tmp_path / "worker-ice"
    invite_path = tmp_path / "worker-ice.invite.json"
    room = "A" * 43
    assert main(["init", "--data-dir", str(worker_dir), "--port", "47893"]) == 0
    assert main([
        "invite", "--data-dir", str(worker_dir), "--host", "worker.example.net",
        "--name", "ice-worker", "--output", str(invite_path),
        "--ice-signal-url", "https://signal.example.net", "--ice-room", room,
    ]) == 0
    peer = load_invite(str(invite_path))
    assert peer.ice_signal_url == "https://signal.example.net"
    assert peer.ice_room == room
    assert peer.to_dict()["ice_room"] == room
