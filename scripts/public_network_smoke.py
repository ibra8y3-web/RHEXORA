#!/usr/bin/env python3
"""External smoke checks for GitHub Actions against operator-owned public infrastructure."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import sys
import argparse
import tempfile
import secrets
from pathlib import Path
from typing import Any

from rhexora.client import Peer, RhexoraClient
from rhexora.dht import DHTNode, verify_record
from rhexora.stun import discover_mapped_address


def _tcp_relay_check(host: str, port: int) -> dict[str, Any]:
    with socket.create_connection((host, port), timeout=8) as sock:
        sock.settimeout(8)
        stream = sock.makefile("rb")
        greeting = stream.readline(128)
        challenge = stream.readline(256)
        if greeting != b"RHEXORA-RELAY/1\n" or not challenge.startswith(b"CHALLENGE "):
            raise RuntimeError("public endpoint did not return the RHEXORA relay greeting/challenge")
        return {"status": "reachable", "host": host, "port": port, "challenge_received": True}


def _load_encoded_invite(encoded_invite: str) -> Peer:
    try:
        raw = base64.b64decode(encoded_invite, validate=True)
        invite = json.loads(raw.decode("utf-8"))
        return Peer.from_dict(invite)
    except Exception as exc:
        raise RuntimeError(f"RHEXORA_PUBLIC_ICE_INVITE_B64 is not a valid base64-encoded peer invite: {exc}") from exc


async def _compute_check(peer: Peer, transport: str) -> dict[str, Any]:
    client = RhexoraClient(timeout=60)
    async def reject_direct_fallback(_peer: Peer, _request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"{transport} acceptance must not pass through direct TCP fallback")
    # These acceptance checks must prove the named transport, not silently pass by direct TCP.
    client._exchange_direct = reject_direct_fallback  # type: ignore[method-assign]
    result = await client.execute(peer, "math.count_primes", {"start": 1, "stop": 1000})
    count = result.get("result", {}).get("count")
    if count != 168:
        raise RuntimeError(f"{transport} tunnel returned an unexpected computation result: {count!r}")
    return {"status": "passed", "transport": transport, "result": count, "worker": peer.name}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test RHEXORA against operator-owned public infrastructure")
    parser.add_argument("--strict", action="store_true", help="Fail when any public transport or bootstrap check is not configured")
    args = parser.parse_args(argv)
    report: dict[str, Any] = {"checks": {}, "skipped": []}
    stun_server = os.getenv("RHEXORA_STUN_SERVER", "stun.l.google.com")
    stun_port = int(os.getenv("RHEXORA_STUN_PORT", "19302"))
    stun_mapping: dict[str, Any] | None = None
    try:
        stun_mapping = discover_mapped_address(stun_server, stun_port, timeout=5)
        report["checks"]["stun"] = {"status": "passed", **stun_mapping}
    except Exception as exc:
        report["checks"]["stun"] = {"status": "failed", "error": str(exc)}

    relay_host = os.getenv("RHEXORA_PUBLIC_RELAY_HOST", "")
    relay_port = int(os.getenv("RHEXORA_PUBLIC_RELAY_PORT", "47825"))
    if relay_host:
        try:
            report["checks"]["relay"] = _tcp_relay_check(relay_host, relay_port)
        except Exception as exc:
            report["checks"]["relay"] = {"status": "failed", "error": str(exc)}
    else:
        report["skipped"].append("relay: set RHEXORA_PUBLIC_RELAY_HOST and RHEXORA_PUBLIC_RELAY_PORT")

    bootstrap_values = [item.strip() for item in os.getenv("RHEXORA_DHT_BOOTSTRAP", "").split(",") if item.strip()]
    if bootstrap_values:
        async def check_dht() -> list[dict[str, Any]]:
            import socket as _socket
            results: list[dict[str, Any]] = []
            with tempfile.TemporaryDirectory(prefix="rhexora-public-dht-") as temporary:
                import ipaddress
                if not stun_mapping:
                    raise RuntimeError("DHT public probe requires a successful STUN mapped-address check")
                public_host = str(stun_mapping.get("host", ""))
                try:
                    public_address = ipaddress.ip_address(public_host)
                except ValueError as exc:
                    raise RuntimeError("STUN did not return a valid public IP address for the DHT probe") from exc
                if public_address.version != 4 or not public_address.is_global:
                    raise RuntimeError("The current public DHT probe requires a globally routable IPv4 STUN mapping")
                with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as udp:
                    udp.bind(("0.0.0.0", 0))
                    local_port = int(udp.getsockname()[1])
                node = DHTNode(Path(temporary), public_host, port=local_port, bind_host="0.0.0.0", request_timeout=4.0)
                await node.start()
                try:
                    for endpoint in bootstrap_values:
                        host, sep, raw_port = endpoint.rpartition(":")
                        if not sep or not host or not raw_port.isdigit() or not 1 <= int(raw_port) <= 65535:
                            results.append({"endpoint": endpoint, "status": "failed", "error": "expected HOST:PORT"})
                            continue
                        try:
                            address = (host, int(raw_port))
                            response = await node._send_request(address, "health", target=secrets.token_hex(20))
                            if response.get("type") != "healthy":
                                raise RuntimeError("bootstrap node health response was invalid")
                            records = response.get("records", [])
                            valid_records = [record for record in records if verify_record(record)] if isinstance(records, list) else []
                            if not valid_records:
                                raise RuntimeError("bootstrap node returned no valid signed routing neighbors; deploy and connect multiple DHT seeds")
                            # Confirm at least one returned neighbor is reachable, not merely signed.
                            neighbor = None
                            neighbor_errors: list[str] = []
                            for candidate in valid_records:
                                try:
                                    neighbor_health = await node._send_request(
                                        (candidate["host"], int(candidate["port"])),
                                        "health",
                                        expected_node_id=candidate["node_id"],
                                    )
                                    if neighbor_health.get("type") == "healthy":
                                        neighbor = candidate
                                        break
                                    neighbor_errors.append(f"{candidate['node_id']}: invalid health response")
                                except Exception as neighbor_error:
                                    neighbor_errors.append(f"{candidate['node_id']}: {neighbor_error}")
                            if neighbor is None:
                                raise RuntimeError("no signed routing neighbor was reachable: " + "; ".join(neighbor_errors))
                            results.append({
                                "endpoint": endpoint, "status": "passed",
                                "node_id": response.get("sender", {}).get("node_id"),
                                "routing_neighbors": len(valid_records),
                                "reachable_neighbor": {"host": neighbor["host"], "port": neighbor["port"], "node_id": neighbor["node_id"]},
                            })
                        except Exception as exc:
                            results.append({"endpoint": endpoint, "status": "failed", "error": str(exc)})
                finally:
                    await node.close()
            return results
        try:
            dht_results = asyncio.run(check_dht())
            report["checks"]["dht_bootstrap"] = {"status": "passed" if dht_results and all(item["status"] == "passed" for item in dht_results) else "failed", "nodes": dht_results}
        except Exception as exc:
            report["checks"]["dht_bootstrap"] = {"status": "failed", "error": str(exc)}
    else:
        report["skipped"].append("DHT bootstrap: set RHEXORA_DHT_BOOTSTRAP as comma-separated HOST:PORT endpoints")

    invite = os.getenv("RHEXORA_PUBLIC_ICE_INVITE_B64", "")
    if invite:
        try:
            peer = _load_encoded_invite(invite)
        except Exception as exc:
            report["checks"]["peer_invite"] = {"status": "failed", "error": str(exc)}
        else:
            from dataclasses import replace
            if peer.ice_signal_url:
                ice_peer = replace(peer, relay_host="", relay_port=0, relay_token="")
                try:
                    report["checks"]["ice_end_to_end"] = asyncio.run(_compute_check(ice_peer, "WebRTC ICE DataChannel + pinned TLS"))
                except Exception as exc:
                    report["checks"]["ice_end_to_end"] = {"status": "failed", "error": str(exc)}
            else:
                report["skipped"].append("ICE end-to-end: supplied invite has no ICE signaling settings")
            if peer.relay_host:
                relay_peer = replace(peer, ice_signal_url="", ice_room="")
                try:
                    report["checks"]["relay_end_to_end"] = asyncio.run(_compute_check(relay_peer, "opaque Relay + pinned TLS"))
                except Exception as exc:
                    report["checks"]["relay_end_to_end"] = {"status": "failed", "error": str(exc)}
            else:
                report["skipped"].append("Relay end-to-end: supplied invite has no Relay endpoint settings")
    else:
        report["skipped"].append("ICE end-to-end: set RHEXORA_PUBLIC_ICE_INVITE_B64 and keep the matching worker online")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    failures = [name for name, value in report["checks"].items() if value.get("status") == "failed"]
    if failures:
        print(f"Public network checks failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    if not report["checks"]:
        print("No external checks were configured", file=sys.stderr)
        return 2
    if args.strict and report["skipped"]:
        print("Required public checks were skipped: " + "; ".join(report["skipped"]), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
