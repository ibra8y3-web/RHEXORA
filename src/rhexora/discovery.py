"""Best-effort LAN peer discovery over IPv4 multicast; never grants trust."""

from __future__ import annotations

import json
import socket
import time
from typing import Any

from .server import DISCOVERY_GROUP, DISCOVERY_PORT


def discover_peers(timeout: float = 3.0) -> list[dict[str, Any]]:
    if timeout <= 0 or timeout > 30:
        raise ValueError("Discovery timeout must be between 0 and 30 seconds")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    found: dict[str, dict[str, Any]] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        membership = socket.inet_aton(DISCOVERY_GROUP) + socket.inet_aton("0.0.0.0")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.settimeout(min(timeout, 0.5))
        query = json.dumps({"kind": "discover", "protocol_version": 1}).encode("utf-8")
        sock.sendto(query, (DISCOVERY_GROUP, DISCOVERY_PORT))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                packet = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(packet, dict) or packet.get("kind") != "peer":
                continue
            if packet.get("protocol_version") != 1:
                continue
            node_id = packet.get("node_id")
            port = packet.get("tcp_port")
            fingerprint = packet.get("tls_fingerprint")
            if not isinstance(node_id, str) or not isinstance(port, int) or not isinstance(fingerprint, str):
                continue
            found[node_id] = {
                "node_id": node_id,
                "host": address[0],
                "port": port,
                "tls_fingerprint": fingerprint,
                "capabilities": packet.get("capabilities", []),
            }
    finally:
        sock.close()
    return sorted(found.values(), key=lambda item: item["node_id"])
