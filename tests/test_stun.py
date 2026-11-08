import socket
import struct

import pytest

from rhexora.stun import MAGIC_COOKIE, build_binding_request, parse_binding_response


def test_parse_xor_mapped_ipv4_response():
    _, txid = build_binding_request(b"0123456789ab")
    ip = socket.inet_aton("203.0.113.9")
    xip = bytes(a ^ b for a, b in zip(ip, struct.pack("!I", MAGIC_COOKIE)))
    xport = 54321 ^ (MAGIC_COOKIE >> 16)
    value = b"\x00\x01" + struct.pack("!H", xport) + xip
    attr = struct.pack("!HH", 0x0020, len(value)) + value
    packet = struct.pack("!HHI", 0x0101, len(attr), MAGIC_COOKIE) + txid + attr
    assert parse_binding_response(packet, txid) == {"host": "203.0.113.9", "port": 54321, "attribute": "xor-mapped"}


def test_stun_rejects_wrong_transaction_id():
    _, txid = build_binding_request(b"0123456789ab")
    packet = struct.pack("!HHI", 0x0101, 0, MAGIC_COOKIE) + b"abcdefghijkl"
    with pytest.raises(ValueError, match="transaction ID"):
        parse_binding_response(packet, txid)
