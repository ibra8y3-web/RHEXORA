"""RFC 5389 STUN binding discovery (mapping diagnostics, not a NAT traversal guarantee)."""
from __future__ import annotations

import secrets
import socket
import struct
from typing import Any

MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101
XOR_MAPPED_ADDRESS = 0x0020
MAPPED_ADDRESS = 0x0001


def build_binding_request(transaction_id: bytes | None = None) -> tuple[bytes, bytes]:
    txid = transaction_id or secrets.token_bytes(12)
    if len(txid) != 12:
        raise ValueError("STUN transaction ID must be 12 bytes")
    return struct.pack("!HHI", BINDING_REQUEST, 0, MAGIC_COOKIE) + txid, txid


def parse_binding_response(data: bytes, transaction_id: bytes) -> dict[str, Any]:
    if len(data) < 20:
        raise ValueError("STUN response is shorter than its header")
    msg_type, msg_len, cookie = struct.unpack("!HHI", data[:8])
    if msg_type != BINDING_SUCCESS or cookie != MAGIC_COOKIE or data[8:20] != transaction_id:
        raise ValueError("STUN response type, cookie or transaction ID is invalid")
    if msg_len != len(data) - 20 or msg_len % 4:
        raise ValueError("STUN response length is invalid")
    offset = 20
    mapped = None
    while offset + 4 <= len(data):
        attr_type, attr_len = struct.unpack("!HH", data[offset:offset + 4])
        offset += 4
        if offset + attr_len > len(data):
            raise ValueError("STUN attribute exceeds packet length")
        value = data[offset:offset + attr_len]
        padded = (attr_len + 3) & ~3
        offset += padded
        if attr_type not in (XOR_MAPPED_ADDRESS, MAPPED_ADDRESS) or attr_len not in (8, 20):
            continue
        family = value[1]
        port = struct.unpack("!H", value[2:4])[0]
        if attr_type == XOR_MAPPED_ADDRESS:
            port ^= (MAGIC_COOKIE >> 16)
        if family == 1 and attr_len == 8:
            raw = value[4:8]
            if attr_type == XOR_MAPPED_ADDRESS:
                raw = bytes(a ^ b for a, b in zip(raw, struct.pack("!I", MAGIC_COOKIE)))
            address = socket.inet_ntop(socket.AF_INET, raw)
        elif family == 2 and attr_len == 20:
            raw = value[4:20]
            if attr_type == XOR_MAPPED_ADDRESS:
                mask = struct.pack("!I", MAGIC_COOKIE) + transaction_id
                raw = bytes(a ^ b for a, b in zip(raw, mask))
            address = socket.inet_ntop(socket.AF_INET6, raw)
        else:
            continue
        mapped = {"host": address, "port": port, "attribute": "xor-mapped" if attr_type == XOR_MAPPED_ADDRESS else "mapped"}
        if attr_type == XOR_MAPPED_ADDRESS:
            return mapped
    if mapped:
        return mapped
    raise ValueError("STUN response has no supported mapped-address attribute")


def discover_mapped_address(server: str = "stun.l.google.com", port: int = 19302, *, timeout: float = 3.0) -> dict[str, Any]:
    if not (1 <= port <= 65535) or timeout <= 0 or timeout > 15:
        raise ValueError("Invalid STUN port or timeout")
    infos = socket.getaddrinfo(server, port, type=socket.SOCK_DGRAM)
    if not infos:
        raise OSError("Could not resolve STUN server")
    family, socktype, proto, _, address = infos[0]
    request, txid = build_binding_request()
    with socket.socket(family, socktype, proto) as sock:
        sock.settimeout(timeout)
        sock.sendto(request, address)
        response, _ = sock.recvfrom(2048)
    return parse_binding_response(response, txid)
