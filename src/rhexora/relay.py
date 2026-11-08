"""Optional self-hosted, end-to-end opaque TCP relay for NAT-restricted peers.

The relay authenticates worker/requester access with a one-use HMAC challenge,
then forwards the requester's TLS byte stream without terminating that TLS.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .identity import load_json_file

LOG = logging.getLogger("rhexora.relay")
MAX_CONTROL_LINE = 4096
MAX_PENDING_WORKERS_PER_NODE = 16


def make_proof(token: str, action: str, node_id: str, nonce: str) -> str:
    message = f"{action}|{node_id}|{nonce}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def load_relay_credentials(path: str | Path) -> dict[str, str]:
    raw = load_json_file(Path(path).expanduser().resolve())
    if not isinstance(raw, dict) or not raw:
        raise ConfigurationError("Relay credentials file must be a non-empty JSON object mapping node_id to token")
    credentials: dict[str, str] = {}
    for node_id, token in raw.items():
        if not isinstance(node_id, str) or not node_id or not isinstance(token, str) or len(token) < 24:
            raise ConfigurationError("Relay credentials contain an invalid node ID or token")
        credentials[node_id] = token
    return credentials


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        if writer.can_write_eof():
            try:
                writer.write_eof()
                await writer.drain()
            except (ConnectionError, OSError):
                pass


async def _bridge(
    left_reader: asyncio.StreamReader,
    left_writer: asyncio.StreamWriter,
    right_reader: asyncio.StreamReader,
    right_writer: asyncio.StreamWriter,
) -> None:
    tasks = [
        asyncio.create_task(_pump(left_reader, right_writer)),
        asyncio.create_task(_pump(right_reader, left_writer)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        for writer in (left_writer, right_writer):
            writer.close()
        await asyncio.gather(left_writer.wait_closed(), right_writer.wait_closed(), return_exceptions=True)


class RhexoraRelayServer:
    """A small self-hosted rendezvous and opaque byte relay."""
    def __init__(self, credentials: dict[str, str], *, worker_wait_timeout: float = 30.0) -> None:
        self.credentials = dict(credentials)
        self.worker_wait_timeout = worker_wait_timeout
        self.server: asyncio.AbstractServer | None = None
        self.workers: dict[str, asyncio.Queue[tuple[asyncio.StreamReader, asyncio.StreamWriter]]] = defaultdict(asyncio.Queue)

    async def start(self, host: str = "0.0.0.0", port: int = 47825) -> int:
        self.server = await asyncio.start_server(self._handle, host, port, limit=MAX_CONTROL_LINE)
        sockets = self.server.sockets or []
        return int(sockets[0].getsockname()[1]) if sockets else port

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        paired_worker: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        try:
            writer.write(b"RHEXORA-RELAY/1\n")
            nonce = secrets.token_hex(24)
            writer.write(f"CHALLENGE {nonce}\n".encode("ascii"))
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=10)
            if len(raw) > MAX_CONTROL_LINE:
                return
            try:
                packet = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if not isinstance(packet, dict):
                return
            node_id, action = packet.get("node_id"), packet.get("action")
            proof, timestamp = packet.get("proof"), packet.get("timestamp")
            if not isinstance(node_id, str) or action not in {"worker", "connect"}:
                return
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or abs(int(time.time()) - timestamp) > 60:
                return
            token = self.credentials.get(node_id)
            if not token or not isinstance(proof, str):
                return
            expected = make_proof(token, action, node_id, nonce)
            if not hmac.compare_digest(expected, proof):
                return

            if action == "worker":
                queue = self.workers[node_id]
                if queue.qsize() >= MAX_PENDING_WORKERS_PER_NODE:
                    writer.write(b"BUSY\n")
                    await writer.drain()
                    return
                await queue.put((reader, writer))
                # The requester handler owns the byte stream after pairing and closes it.
                await writer.wait_closed()
                return

            queue = self.workers[node_id]
            try:
                paired_worker = await asyncio.wait_for(queue.get(), timeout=self.worker_wait_timeout)
            except asyncio.TimeoutError:
                writer.write(b"NO_WORKER\n")
                await writer.drain()
                return
            worker_reader, worker_writer = paired_worker
            if worker_writer.is_closing():
                writer.write(b"NO_WORKER\n")
                await writer.drain()
                return
            worker_writer.write(b"SESSION\n")
            writer.write(b"READY\n")
            await asyncio.gather(worker_writer.drain(), writer.drain())
            await _bridge(reader, writer, worker_reader, worker_writer)
            paired_worker = None
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        except Exception:
            LOG.exception("Relay session failed")
        finally:
            if paired_worker is not None:
                paired_worker[1].close()
                await asyncio.gather(paired_worker[1].wait_closed(), return_exceptions=True)
            if not writer.is_closing():
                writer.close()
                await asyncio.gather(writer.wait_closed(), return_exceptions=True)

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None


async def run_relay_worker(
    data_dir: str | Path,
    relay_host: str,
    relay_port: int,
    *,
    local_host: str = "127.0.0.1",
    local_port: int | None = None,
    reconnect_seconds: float = 2.0,
) -> None:
    """Keep an outbound tunnel to a relay and forward paired bytes to the local TLS worker."""
    config = load_json_file(Path(data_dir).expanduser().resolve() / "node.json")
    if not isinstance(config, dict) or not config.get("node_id") or not config.get("auth_token"):
        raise ConfigurationError("A valid local node.json is required for relay-worker")
    node_id = str(config["node_id"])
    token = str(config.get("relay_token", ""))
    if len(token) < 24:
        raise ConfigurationError("This node has no independent relay_token; initialize a new node before starting relay-worker")
    destination_port = int(local_port if local_port is not None else config.get("listen_port", 47821))
    if not (1 <= relay_port <= 65535 and 1 <= destination_port <= 65535):
        raise ConfigurationError("Relay and local worker ports must be valid TCP ports")

    while True:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(relay_host, relay_port), timeout=15)
            greeting = await asyncio.wait_for(reader.readline(), timeout=10)
            challenge = await asyncio.wait_for(reader.readline(), timeout=10)
            if greeting != b"RHEXORA-RELAY/1\n" or not challenge.startswith(b"CHALLENGE "):
                raise ConnectionError("Relay protocol greeting is invalid")
            nonce = challenge.decode("ascii", errors="strict").strip().split(" ", 1)[1]
            proof = make_proof(token, "worker", node_id, nonce)
            auth = json.dumps({"action": "worker", "node_id": node_id, "timestamp": int(time.time()), "proof": proof}, separators=(",", ":"))
            writer.write(auth.encode("utf-8") + b"\n")
            await writer.drain()
            session = await asyncio.wait_for(reader.readline(), timeout=300)
            if session != b"SESSION\n":
                writer.close()
                await writer.wait_closed()
                await asyncio.sleep(reconnect_seconds)
                continue
            local_reader, local_writer = await asyncio.wait_for(asyncio.open_connection(local_host, destination_port), timeout=10)
            try:
                await _bridge(reader, writer, local_reader, local_writer)
            except Exception:
                local_writer.close()
                writer.close()
                await asyncio.gather(local_writer.wait_closed(), writer.wait_closed(), return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except (OSError, asyncio.TimeoutError, ConnectionError, ValueError) as exc:
            LOG.warning("Relay worker tunnel unavailable (%s); reconnecting", exc)
            await asyncio.sleep(reconnect_seconds)
