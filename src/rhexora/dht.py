"""A small signed-record Kademlia-style UDP DHT for peer endpoint discovery.

This module discovers signed endpoint records only. It never grants task-execution
trust; task submission still requires a separately exchanged TLS pin and token.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import ipaddress
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DHT_VERSION = 1
ID_BYTES = 20
ID_HEX_LEN = ID_BYTES * 2
K = 8
ALPHA = 3
MAX_DATAGRAM = 1200
MAX_RECORD_TTL = 7 * 24 * 60 * 60
MAX_STORED_RECORDS = 10_000
DHT_SOURCE_RATE_PER_SECOND = 30.0
DHT_SOURCE_BURST = 60.0
DHT_MAX_TRACKED_SOURCES = 4096


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _node_id(public_key: bytes) -> str:
    return hashlib.sha256(public_key).digest()[:ID_BYTES].hex()


def load_or_create_key(data_dir: str | Path) -> Ed25519PrivateKey:
    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "dht-ed25519-private.pem"
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("DHT private key has an unexpected type")
        return key
    key = Ed25519PrivateKey.generate()
    encoded = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    return key


def make_record(key: Ed25519PrivateKey, host: str, port: int, *, seq: int = 1, ttl: int = 3600) -> dict[str, Any]:
    if not host or len(host) > 253 or any(ch.isspace() for ch in host):
        raise ValueError("Advertised host must be a valid hostname or IP address")
    if not (1 <= int(port) <= 65535):
        raise ValueError("Advertised port must be between 1 and 65535")
    if isinstance(seq, bool) or int(seq) < 1:
        raise ValueError("Record sequence must be a positive integer")
    if not (60 <= int(ttl) <= MAX_RECORD_TTL):
        raise ValueError(f"Record TTL must be between 60 and {MAX_RECORD_TTL} seconds")
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    unsigned = {
        "node_id": _node_id(public),
        "public_key": base64.b64encode(public).decode("ascii"),
        "host": host,
        "port": int(port),
        "seq": int(seq),
        "expires_at": int(time.time()) + int(ttl),
    }
    signature = key.sign(_canonical(unsigned))
    return {**unsigned, "signature": base64.b64encode(signature).decode("ascii")}


def verify_record(record: Any, *, now: int | None = None) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        unsigned = {key: record[key] for key in ("node_id", "public_key", "host", "port", "seq", "expires_at")}
        if not isinstance(unsigned["host"], str) or not unsigned["host"] or len(unsigned["host"]) > 253:
            return False
        if any(ch.isspace() for ch in unsigned["host"]):
            return False
        if isinstance(unsigned["port"], bool) or not isinstance(unsigned["port"], int) or not 1 <= unsigned["port"] <= 65535:
            return False
        if isinstance(unsigned["seq"], bool) or not isinstance(unsigned["seq"], int) or unsigned["seq"] < 1:
            return False
        current = int(time.time()) if now is None else int(now)
        expiry = unsigned["expires_at"]
        if isinstance(expiry, bool) or not isinstance(expiry, int) or expiry <= current or expiry > current + MAX_RECORD_TTL + 60:
            return False
        public = base64.b64decode(unsigned["public_key"], validate=True)
        signature = base64.b64decode(record["signature"], validate=True)
        if len(public) != 32 or len(signature) != 64 or unsigned["node_id"] != _node_id(public):
            return False
        Ed25519PublicKey.from_public_bytes(public).verify(signature, _canonical(unsigned))
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    except Exception:
        return False


def xor_distance(left: str, right: str) -> int:
    if len(left) != ID_HEX_LEN or len(right) != ID_HEX_LEN:
        raise ValueError("DHT IDs must be 160-bit hexadecimal strings")
    try:
        return int(left, 16) ^ int(right, 16)
    except ValueError as exc:
        raise ValueError("DHT IDs must be hexadecimal") from exc


class RoutingTable:
    """160 LRU buckets, each holding at most K authenticated node records."""
    def __init__(self, local_id: str, *, bucket_size: int = K) -> None:
        if len(local_id) != ID_HEX_LEN:
            raise ValueError("local_id must be a 160-bit hexadecimal ID")
        int(local_id, 16)
        self.local_id = local_id
        self.bucket_size = bucket_size
        self.buckets: list[list[dict[str, Any]]] = [[] for _ in range(ID_BYTES * 8)]

    def _bucket_index(self, node_id: str) -> int:
        distance = xor_distance(self.local_id, node_id)
        if distance == 0:
            return -1
        return distance.bit_length() - 1

    def add(self, record: dict[str, Any]) -> bool:
        if not verify_record(record):
            return False
        node_id = record["node_id"]
        index = self._bucket_index(node_id)
        if index < 0:
            return False
        bucket = self.buckets[index]
        for old in bucket:
            if old["node_id"] == node_id:
                old_seq = int(old.get("seq", 0))
                new_seq = int(record.get("seq", 0))
                if new_seq <= old_seq:
                    return False
                bucket.remove(old)
                bucket.append(record)
                return True
        if len(bucket) >= self.bucket_size:
            return False
        bucket.append(record)
        return True

    def closest(self, target: str, count: int = K) -> list[dict[str, Any]]:
        if len(target) != ID_HEX_LEN:
            raise ValueError("target must be a 160-bit hexadecimal ID")
        records = [record for bucket in self.buckets for record in bucket if verify_record(record)]
        return sorted(records, key=lambda item: xor_distance(item["node_id"], target))[: max(0, count)]


class DHTStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS peer_records (node_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, expires_at INTEGER NOT NULL)")
            db.execute("DELETE FROM peer_records WHERE expires_at <= ?", (int(time.time()),))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def put(self, record: dict[str, Any]) -> bool:
        if not verify_record(record):
            return False
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self.path, timeout=5) as db:
            row = db.execute("SELECT record_json FROM peer_records WHERE node_id=?", (record["node_id"],)).fetchone()
            if row:
                old = json.loads(row[0])
                if int(old.get("seq", 0)) >= int(record["seq"]):
                    return False
            count = db.execute("SELECT COUNT(*) FROM peer_records").fetchone()[0]
            exists = row is not None
            if not exists and count >= MAX_STORED_RECORDS:
                db.execute("DELETE FROM peer_records WHERE node_id IN (SELECT node_id FROM peer_records ORDER BY expires_at ASC LIMIT ?)", (max(1, count - MAX_STORED_RECORDS + 1),))
            db.execute("INSERT INTO peer_records(node_id,record_json,expires_at) VALUES(?,?,?) ON CONFLICT(node_id) DO UPDATE SET record_json=excluded.record_json,expires_at=excluded.expires_at", (record["node_id"], encoded, record["expires_at"]))
        return True

    def get(self, node_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT record_json,expires_at FROM peer_records WHERE node_id=?", (node_id,)).fetchone()
        if not row or row[1] <= int(time.time()):
            return None
        record = json.loads(row[0])
        return record if verify_record(record) else None

    def all(self) -> list[dict[str, Any]]:
        now = int(time.time())
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT record_json FROM peer_records WHERE expires_at>?", (now,)).fetchall()
        return [record for row in rows if verify_record(record := json.loads(row[0]))]


class _DHTProtocol(asyncio.DatagramProtocol):
    def __init__(self, node: "DHTNode") -> None:
        self.node = node
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > MAX_DATAGRAM or not self.node.allow_inbound_datagram(addr):
            return
        try:
            packet = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(packet, dict) or packet.get("v") != DHT_VERSION:
            return
        asyncio.create_task(self.node._handle_packet(packet, addr))

    def error_received(self, exc: Exception) -> None:
        self.node.last_error = str(exc)


class DHTNode:
    def __init__(self, data_dir: str | Path, advertise_host: str, *, port: int = 47823, bind_host: str = "0.0.0.0", record_ttl: int = 3600, request_timeout: float = 1.5, allow_private_endpoints: bool = False) -> None:
        self.root = Path(data_dir).expanduser().resolve()
        self.key = load_or_create_key(self.root)
        self.advertise_host = advertise_host
        self.record_ttl = record_ttl
        self.record = make_record(self.key, advertise_host, port, ttl=record_ttl)
        self.table = RoutingTable(self.record["node_id"])
        self.store = DHTStore(self.root / "dht-peers.sqlite3")
        self.transport: asyncio.DatagramTransport | None = None
        self.bind_host = bind_host
        self.bind_port = port
        self.request_timeout = request_timeout
        self.allow_private_endpoints = allow_private_endpoints
        self.pending: dict[str, tuple[asyncio.Future[dict[str, Any]], str | None]] = {}
        self.last_error: str | None = None
        self.bootstrap_endpoints: set[tuple[str, int]] = set()
        self._maintenance_task: asyncio.Task[None] | None = None
        self._source_buckets: dict[str, tuple[float, float]] = {}

    @property
    def local_id(self) -> str:
        return self.record["node_id"]

    def allow_inbound_datagram(self, addr: tuple[str, int]) -> bool:
        """Apply a bounded token bucket per source IP to limit UDP request floods."""
        source = str(addr[0])
        now = time.monotonic()
        previous = self._source_buckets.get(source)
        if previous is None:
            if len(self._source_buckets) >= DHT_MAX_TRACKED_SOURCES:
                stale = [key for key, (_, seen) in self._source_buckets.items() if now - seen > 120]
                for key in stale:
                    self._source_buckets.pop(key, None)
                if len(self._source_buckets) >= DHT_MAX_TRACKED_SOURCES:
                    oldest = min(self._source_buckets, key=lambda key: self._source_buckets[key][1])
                    self._source_buckets.pop(oldest, None)
            tokens = DHT_SOURCE_BURST
        else:
            old_tokens, last_seen = previous
            tokens = min(DHT_SOURCE_BURST, old_tokens + max(0.0, now - last_seen) * DHT_SOURCE_RATE_PER_SECOND)
        if tokens < 1.0:
            self._source_buckets[source] = (tokens, now)
            return False
        self._source_buckets[source] = (tokens - 1.0, now)
        return True

    async def start(self) -> tuple[str, int]:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: _DHTProtocol(self), local_addr=(self.bind_host, self.bind_port))
        self.transport = transport  # type: ignore[assignment]
        sockname = self.transport.get_extra_info("sockname")
        self.bind_port = int(sockname[1])
        if self.record["port"] == 47823 and self.bind_port != 47823:
            self.record["port"] = self.bind_port
        self.store.put(self.record)
        for record in self.store.all():
            self.table.add(record)
        self._maintenance_task = asyncio.create_task(self._maintain_records())
        return str(sockname[0]), self.bind_port

    async def _endpoint_is_routable(self, host: str, port: int) -> bool:
        if self.allow_private_endpoints:
            return True
        try:
            address = ipaddress.ip_address(host.strip("[]"))
            return address.is_global
        except ValueError:
            normalized = host.rstrip(".").lower()
            if normalized in {"localhost", "localhost.localdomain"} or normalized.endswith((".local", ".internal", ".lan")):
                return False
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_DGRAM)
            except OSError:
                return False
            resolved = []
            for info in infos:
                try:
                    resolved.append(ipaddress.ip_address(info[4][0]))
                except ValueError:
                    return False
            return bool(resolved) and all(address.is_global for address in resolved)

    async def _send_request(
        self, address: tuple[str, int], kind: str, *, expected_node_id: str | None = None, **fields: Any
    ) -> dict[str, Any]:
        if self.transport is None:
            raise RuntimeError("DHT node is not started")
        if not await self._endpoint_is_routable(address[0], address[1]):
            raise ValueError("DHT endpoint is not publicly routable; enable allow_private_endpoints only for a trusted private overlay")
        request_id = secrets.token_hex(12)
        packet = {"v": DHT_VERSION, "id": request_id, "type": kind, "sender": self.record, **fields}
        data = _canonical(packet)
        if len(data) > MAX_DATAGRAM:
            raise ValueError("DHT packet exceeds UDP datagram limit")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending[request_id] = (future, expected_node_id)
        self.transport.sendto(data, address)
        try:
            response = await asyncio.wait_for(future, timeout=self.request_timeout)
            remote = response.get("sender")
            if not verify_record(remote):
                raise ValueError("DHT response did not contain a valid signed sender record")
            return response
        finally:
            self.pending.pop(request_id, None)

    async def _sender_matches_source(self, record: Any, addr: tuple[str, int]) -> bool:
        """Reject signed DHT records that claim an unrelated source address."""
        if not verify_record(record):
            return False
        advertised = str(record["host"]).strip("[]")
        try:
            return ipaddress.ip_address(advertised) == ipaddress.ip_address(addr[0])
        except ValueError:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(advertised, None, type=socket.SOCK_DGRAM)
            except OSError:
                return False
            resolved = set()
            for info in infos:
                try:
                    resolved.add(ipaddress.ip_address(info[4][0]))
                except ValueError:
                    return False
            try:
                source = ipaddress.ip_address(addr[0])
            except ValueError:
                return False
            return source in resolved

    async def _handle_packet(self, packet: dict[str, Any], addr: tuple[str, int]) -> None:
        if self.transport is None:
            return
        kind = packet.get("type")
        request_id = packet.get("id")
        if not isinstance(request_id, str) or len(request_id) > 48:
            return
        # Responses are correlated locally. Their included peer records are independently signed.
        if kind in {"pong", "nodes", "stored", "healthy", "error"}:
            pending = self.pending.get(request_id)
            if pending:
                future, expected_node_id = pending
                sender = packet.get("sender")
                if not await self._sender_matches_source(sender, addr):
                    return
                if expected_node_id and sender.get("node_id") != expected_node_id:
                    return
                if future and not future.done():
                    future.set_result(packet)
            return
        sender = packet.get("sender")
        if not await self._sender_matches_source(sender, addr):
            return
        response: dict[str, Any] = {"v": DHT_VERSION, "id": request_id, "sender": self.record}
        if kind == "health":
            # Public probes are read-only. An optional target returns this node's closest
            # signed routing entries without inserting the probing client.
            response["type"] = "healthy"
            target = packet.get("target")
            if target is not None:
                if not isinstance(target, str) or len(target) != ID_HEX_LEN:
                    response.update({"type": "error", "error": "invalid target"})
                else:
                    try:
                        int(target, 16)
                    except ValueError:
                        response.update({"type": "error", "error": "invalid target"})
                    else:
                        response["records"] = self.table.closest(target, 3)
        elif kind == "ping":
            self.table.add(sender)
            self.store.put(sender)
            response["type"] = "pong"
        elif kind == "announce":
            self.table.add(sender)
            self.store.put(sender)
            announced = packet.get("record")
            if not verify_record(announced):
                response.update({"type": "error", "error": "invalid signed peer record"})
            else:
                self.store.put(announced)
                self.table.add(announced)
                response["type"] = "stored"
        elif kind == "find_node":
            self.table.add(sender)
            self.store.put(sender)
            target = packet.get("target")
            if not isinstance(target, str) or len(target) != ID_HEX_LEN:
                response.update({"type": "error", "error": "invalid target"})
            else:
                records = self.table.closest(target, 3)
                response.update({"type": "nodes", "records": records})
        else:
            return
        encoded = _canonical(response)
        if len(encoded) <= MAX_DATAGRAM:
            self.transport.sendto(encoded, addr)

    async def bootstrap(self, endpoints: list[tuple[str, int]]) -> int:
        self.bootstrap_endpoints.update(endpoints)
        added = 0
        for host, port in endpoints:
            try:
                response = await self._send_request((host, port), "ping")
                record = response.get("sender")
                if response.get("type") == "pong" and verify_record(record):
                    self.table.add(record)
                    self.store.put(record)
                    ack = await self._send_request((host, port), "announce", record=self.record)
                    if ack.get("type") == "stored":
                        added += 1
            except (OSError, asyncio.TimeoutError, ValueError):
                continue
        return added

    async def lookup(self, target: str, *, max_queries: int = 24) -> list[dict[str, Any]]:
        if len(target) != ID_HEX_LEN:
            raise ValueError("target must be a 160-bit hexadecimal node ID")
        known: dict[str, dict[str, Any]] = {r["node_id"]: r for r in self.table.closest(target, K)}
        queried: set[str] = set()
        for _ in range(max_queries):
            candidates = sorted((r for node_id, r in known.items() if node_id not in queried), key=lambda r: xor_distance(r["node_id"], target))[:ALPHA]
            if not candidates:
                break
            async def query(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
                try:
                    response = await self._send_request(
                        (record["host"], int(record["port"])), "find_node",
                        expected_node_id=record["node_id"], target=target,
                    )
                    return record, response
                except (OSError, asyncio.TimeoutError, ValueError):
                    return record, None
            replies = await asyncio.gather(*(query(record) for record in candidates))
            for record, response in replies:
                queried.add(record["node_id"])
                if not response or response.get("type") != "nodes":
                    continue
                for item in response.get("records", []):
                    if verify_record(item):
                        known[item["node_id"]] = item
                        self.table.add(item)
                        self.store.put(item)
            known.update({r["node_id"]: r for r in self.table.closest(target, K)})
        return sorted(known.values(), key=lambda r: xor_distance(r["node_id"], target))[:K]

    async def _maintain_records(self) -> None:
        interval = max(20, self.record_ttl // 3)
        while True:
            await asyncio.sleep(interval)
            self.record = make_record(
                self.key, self.advertise_host, self.record["port"],
                seq=int(self.record["seq"]) + 1, ttl=self.record_ttl,
            )
            self.store.put(self.record)
            candidates = {(r["host"], int(r["port"])) for r in self.table.closest(self.local_id, K)}
            candidates.update(self.bootstrap_endpoints)
            for endpoint in list(candidates)[:K]:
                try:
                    await self._send_request(endpoint, "announce", record=self.record)
                except (OSError, asyncio.TimeoutError, ValueError):
                    continue

    async def close(self) -> None:
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
            self._maintenance_task = None
        if self.transport:
            self.transport.close()
            self.transport = None
        for future, _expected_node_id in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()
