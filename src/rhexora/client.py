"""Authenticated client for trusted RHEXORA peers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import ssl
import socket
import struct
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from . import PROTOCOL_VERSION
from .errors import ConfigurationError, ProtocolError, RemoteTaskError, SecurityError
from .identity import load_peers
from .protocol import DEFAULT_MAX_FRAME_BYTES, canonical_json, read_message, write_message

MAX_DISTRIBUTED_ITEMS = 1_000_000
MAX_DISTRIBUTED_PRIME_RANGE = 2_000_000


@dataclass(frozen=True)
class Peer:
    name: str
    host: str
    port: int
    tls_fingerprint: str
    token: str
    node_id: str = ""
    max_items: int = 100_000
    relay_host: str = ""
    relay_port: int = 0
    relay_token: str = ""
    ice_signal_url: str = ""
    ice_room: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Peer":
        try:
            peer = cls(
                name=str(raw["name"]),
                host=str(raw["host"]),
                port=int(raw["port"]),
                tls_fingerprint=str(raw["tls_fingerprint"]).lower().replace(":", ""),
                token=str(raw["token"]),
                node_id=str(raw.get("node_id", "")),
                max_items=int(raw.get("max_items", 100_000)),
                relay_host=str(raw.get("relay_host", "")),
                relay_port=int(raw.get("relay_port", 0) or 0),
                relay_token=str(raw.get("relay_token", "")),
                ice_signal_url=str(raw.get("ice_signal_url", "")),
                ice_room=str(raw.get("ice_room", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(f"Invalid peer record: {exc}") from exc
        if not peer.name or not peer.host or not (1 <= peer.port <= 65535):
            raise ConfigurationError("Peer name, host and a valid TCP port are required")
        if len(peer.tls_fingerprint) != 64 or any(c not in "0123456789abcdef" for c in peer.tls_fingerprint):
            raise ConfigurationError("Peer TLS fingerprint must be a SHA-256 hex fingerprint")
        if len(peer.token) < 24:
            raise ConfigurationError("Peer token is too short")
        if peer.max_items < 1:
            raise ConfigurationError("Peer max_items must be positive")
        if bool(peer.relay_host) != bool(peer.relay_port):
            raise ConfigurationError("A relay requires both relay_host and relay_port")
        if peer.relay_host and (not peer.node_id or not (1 <= peer.relay_port <= 65535) or len(peer.relay_token) < 24):
            raise ConfigurationError("Relay peers require a node_id, valid relay port and independent relay token")
        if bool(peer.ice_signal_url) != bool(peer.ice_room):
            raise ConfigurationError("ICE peers require both ice_signal_url and ice_room")
        if peer.ice_room and (len(peer.ice_room) < 32 or len(peer.ice_room) > 128 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for ch in peer.ice_room)):
            raise ConfigurationError("ICE room must be a 256-bit URL-safe token")
        if peer.ice_signal_url:
            parsed_signal = urlsplit(peer.ice_signal_url)
            if parsed_signal.scheme not in {"http", "https", "ws", "wss"} or not parsed_signal.hostname:
                raise ConfigurationError("ICE signaling URL must be an absolute HTTP(S) or WS(S) URL")
            if parsed_signal.scheme in {"http", "ws"} and parsed_signal.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ConfigurationError("Public ICE signaling must use HTTPS/WSS")
        return peer

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "tls_fingerprint": self.tls_fingerprint,
            "token": self.token,
            "node_id": self.node_id,
            "max_items": self.max_items,
        }
        if self.relay_host:
            result["relay_host"] = self.relay_host
            result["relay_port"] = self.relay_port
            result["relay_token"] = self.relay_token
        if self.ice_signal_url:
            result["ice_signal_url"] = self.ice_signal_url
            result["ice_room"] = self.ice_room
        return result


class RhexoraClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
        max_in_flight: int = 16,
    ) -> None:
        if timeout <= 0 or max_in_flight < 1:
            raise ValueError("timeout and max_in_flight must be positive")
        self.timeout = timeout
        self.max_frame_bytes = max_frame_bytes
        self.max_in_flight = max_in_flight

    @staticmethod
    def _recv_line(sock: socket.socket, *, limit: int = 4096) -> bytes:
        data = bytearray()
        while len(data) < limit:
            chunk = sock.recv(1)
            if not chunk:
                raise ConnectionError("Relay closed during its control handshake")
            data.extend(chunk)
            if chunk == b"\n":
                return bytes(data)
        raise ProtocolError("Relay control line exceeds its limit")

    @staticmethod
    def _recv_exact(sock: socket.socket, count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise ProtocolError("Connection closed during a relayed protocol frame")
            data.extend(chunk)
        return bytes(data)

    def _exchange_via_relay(self, peer: Peer, request: dict[str, Any]) -> dict[str, Any]:
        from .relay import make_proof
        raw: socket.socket | None = None
        tls_sock: ssl.SSLSocket | None = None
        try:
            raw = socket.create_connection((peer.relay_host, peer.relay_port), timeout=max(self.timeout, 35.0))
            raw.settimeout(max(self.timeout, 35.0))
            greeting = self._recv_line(raw)
            challenge = self._recv_line(raw)
            if greeting != b"RHEXORA-RELAY/1\n" or not challenge.startswith(b"CHALLENGE "):
                raise RemoteTaskError("Relay protocol handshake was rejected")
            nonce = challenge.decode("ascii", errors="strict").strip().split(" ", 1)[1]
            proof = make_proof(peer.relay_token, "connect", peer.node_id, nonce)
            auth = json.dumps({
                "action": "connect", "node_id": peer.node_id,
                "timestamp": int(time.time()), "proof": proof,
            }, separators=(",", ":")).encode("utf-8") + b"\n"
            raw.sendall(auth)
            status = self._recv_line(raw).strip()
            if status != b"READY":
                if status == b"NO_WORKER":
                    raise RemoteTaskError(f"No relay worker is currently connected for {peer.name}")
                raise RemoteTaskError(f"Relay could not pair this request (status={status.decode('ascii', 'replace')})")

            context = ssl._create_unverified_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            tls_sock = context.wrap_socket(raw, server_hostname=None)
            raw = None  # ownership transferred to SSLSocket
            tls_sock.settimeout(self.timeout)
            certificate = tls_sock.getpeercert(binary_form=True)
            if not certificate:
                raise SecurityError("Relayed worker did not present a TLS certificate")
            observed = hashlib.sha256(certificate).hexdigest()
            if not hmac.compare_digest(observed, peer.tls_fingerprint):
                raise SecurityError(f"TLS fingerprint mismatch for {peer.name} over relay; refusing connection")

            payload = canonical_json(request)
            if not (2 <= len(payload) <= self.max_frame_bytes):
                raise ProtocolError("Relayed request frame exceeds its configured size limit")
            tls_sock.sendall(struct.pack("!I", len(payload)) + payload)
            (length,) = struct.unpack("!I", self._recv_exact(tls_sock, 4))
            if length < 2 or length > self.max_frame_bytes:
                raise ProtocolError(f"Invalid relayed response frame length: {length}")
            response = json.loads(self._recv_exact(tls_sock, length).decode("utf-8"))
            if not isinstance(response, dict):
                raise ProtocolError("Relayed response frame must contain a JSON object")
            return response
        except (OSError, asyncio.TimeoutError, ssl.SSLError, json.JSONDecodeError) as exc:
            raise RemoteTaskError(f"Could not reach peer {peer.name} through relay: {exc}") from exc
        finally:
            if tls_sock is not None:
                try:
                    tls_sock.close()
                except OSError:
                    pass
            if raw is not None:
                try:
                    raw.close()
                except OSError:
                    pass

    async def _exchange_via_ice(self, peer: Peer, request: dict[str, Any]) -> dict[str, Any]:
        """Carry the original pinned TLS session over an ICE-negotiated DataChannel."""
        from .ice import ice_settings_from_environment, open_ice_tunnel

        settings = ice_settings_from_environment()
        try:
            tunnel = await open_ice_tunnel(peer.ice_signal_url, peer.ice_room, timeout=self.timeout, **settings)
        except SecurityError:
            raise
        except Exception as exc:
            raise RemoteTaskError(f"Could not establish ICE transport for {peer.name}: {exc}") from exc
        writer: asyncio.StreamWriter | None = None
        try:
            tls_context = ssl._create_unverified_context()
            tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", tunnel.port, ssl=tls_context, server_hostname=None),
                timeout=self.timeout,
            )
            ssl_object = writer.get_extra_info("ssl_object")
            certificate = ssl_object.getpeercert(binary_form=True) if ssl_object else None
            if not certificate:
                raise SecurityError("ICE peer did not present a TLS certificate")
            observed = hashlib.sha256(certificate).hexdigest()
            if not hmac.compare_digest(observed, peer.tls_fingerprint):
                raise SecurityError(f"TLS fingerprint mismatch for {peer.name} over ICE; refusing connection")
            await write_message(writer, request, max_frame_bytes=self.max_frame_bytes)
            return await asyncio.wait_for(read_message(reader, max_frame_bytes=self.max_frame_bytes), timeout=self.timeout)
        except (OSError, asyncio.TimeoutError, ssl.SSLError) as exc:
            raise RemoteTaskError(f"Could not reach peer {peer.name} through ICE: {exc}") from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            await tunnel.close()

    async def _exchange_direct(self, peer: Peer, request: dict[str, Any]) -> dict[str, Any]:
        tls_context = ssl._create_unverified_context()
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(peer.host, peer.port, ssl=tls_context, server_hostname=None),
                timeout=self.timeout,
            )
            ssl_object = writer.get_extra_info("ssl_object")
            certificate = ssl_object.getpeercert(binary_form=True) if ssl_object else None
            if not certificate:
                raise SecurityError("Peer did not present a TLS certificate")
            observed = hashlib.sha256(certificate).hexdigest()
            if not hmac.compare_digest(observed, peer.tls_fingerprint):
                raise SecurityError(f"TLS fingerprint mismatch for {peer.name}; refusing connection")
            await write_message(writer, request, max_frame_bytes=self.max_frame_bytes)
            return await asyncio.wait_for(read_message(reader, max_frame_bytes=self.max_frame_bytes), timeout=self.timeout)
        except (OSError, asyncio.TimeoutError, ssl.SSLError) as exc:
            raise RemoteTaskError(f"Could not reach peer {peer.name} directly: {exc}") from exc
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def _exchange(self, peer: Peer, request: dict[str, Any]) -> dict[str, Any]:
        transport_errors: list[str] = []
        if peer.ice_signal_url:
            try:
                return await self._exchange_via_ice(peer, request)
            except RemoteTaskError as exc:
                transport_errors.append(f"ICE: {exc}")
                # A transport negotiation failure is recoverable; a TLS pin mismatch is not.
        if peer.relay_host:
            try:
                return await asyncio.to_thread(self._exchange_via_relay, peer, request)
            except RemoteTaskError as exc:
                transport_errors.append(f"Relay: {exc}")
        try:
            return await self._exchange_direct(peer, request)
        except RemoteTaskError as exc:
            if transport_errors:
                raise RemoteTaskError("All configured transports failed — " + "; ".join(transport_errors + [f"Direct: {exc}"])) from exc
            raise

    async def execute(
        self,
        peer: Peer,
        task_type: str,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        task = {"type": task_type, "input": payload}
        request_id = request_id or str(uuid.uuid4())
        if not isinstance(request_id, str) or not (1 <= len(request_id) <= 80):
            raise ValueError("request_id must contain between 1 and 80 characters")
        request = {
            "kind": "execute",
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "token": peer.token,
            "task": task,
        }

        response: dict[str, Any] | None = None
        last_transport_error: RemoteTaskError | None = None
        for attempt in range(3):
            try:
                response = await self._exchange(peer, request)
                last_transport_error = None
            except RemoteTaskError as exc:
                last_transport_error = exc
                if attempt == 2:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
                continue

            if response.get("retryable"):
                if attempt == 2:
                    raise RemoteTaskError(str(response.get("error", "Task is still running")))
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            break

        if response is None:
            raise last_transport_error or RemoteTaskError("No response received from peer")
        if response.get("kind") != "result" or response.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError("Peer returned an unsupported response frame")
        if response.get("request_id") != request_id:
            raise ProtocolError("Peer returned a response for a different request")
        if peer.node_id and response.get("node_id") != peer.node_id:
            raise SecurityError("Peer node ID does not match the trusted invitation")
        if not response.get("ok"):
            raise RemoteTaskError(str(response.get("error", "Peer rejected task")))
        expected_task_hash = hashlib.sha256(canonical_json(task)).hexdigest()
        if response.get("task_hash") != expected_task_hash:
            raise SecurityError("Peer response does not match the submitted task")
        result = response.get("result")
        expected_result_hash = hashlib.sha256(canonical_json(result)).hexdigest()
        if response.get("result_hash") != expected_result_hash:
            raise SecurityError("Peer result integrity check failed")
        if not isinstance(result, dict):
            raise ProtocolError("Peer returned a non-object task result")
        return {
            "node_id": response.get("node_id", peer.node_id),
            "result": result,
            "task_hash": expected_task_hash,
            "result_hash": expected_result_hash,
            "request_id": request_id,
        }

    async def _media_request(self, peer: Peer, body: dict[str, Any]) -> dict[str, Any]:
        request = {
            "kind": body.pop("kind", "media.upload"),
            "protocol_version": PROTOCOL_VERSION,
            "request_id": str(uuid.uuid4()),
            "token": peer.token,
            **body,
        }
        response = await self._exchange(peer, request)
        if response.get("protocol_version") != PROTOCOL_VERSION or response.get("request_id") != request["request_id"]:
            raise ProtocolError("Media transfer response does not match the request")
        if response.get("node_id") != (peer.node_id or response.get("node_id")):
            raise SecurityError("Media transfer response came from an unexpected node")
        if not response.get("ok"):
            raise RemoteTaskError(str(response.get("error", "Media transfer was rejected")))
        if response.get("kind") != "media_transfer":
            raise ProtocolError("Peer returned an unsupported media transfer frame")
        return response

    async def upload_media(self, peer: Peer, source: str | Path) -> dict[str, Any]:
        """Upload a bounded media file in authenticated, ordered TLS chunks."""
        path = Path(source).expanduser().resolve(strict=True)
        if not path.is_file() or path.is_symlink():
            raise ValueError("Media source must be a regular file")
        size = path.stat().st_size
        if size <= 0 or size > 512 * 1024 * 1024:
            raise ValueError("Media source must be between 1 byte and 512 MiB")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        sha256 = digest.hexdigest()
        upload_id = uuid.uuid4().hex
        started = await self._media_request(peer, {
            "operation": "start", "upload_id": upload_id, "size": size, "sha256": sha256,
        })
        chunk_bytes = int(started.get("chunk_bytes", 0))
        if not (1 <= chunk_bytes <= 2 * 1024 * 1024):
            raise ProtocolError("Worker returned an invalid media chunk size")
        written = 0
        sequence = 0
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_bytes)
                    if not chunk:
                        break
                    ack = await self._media_request(peer, {
                        "operation": "chunk", "upload_id": upload_id,
                        "sequence": sequence,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    })
                    written += len(chunk)
                    if ack.get("sequence") != sequence or ack.get("written") != written:
                        raise ProtocolError("Worker acknowledged an unexpected media chunk offset")
                    sequence += 1
            finished = await self._media_request(peer, {
                "operation": "finish", "upload_id": upload_id,
            })
            if finished.get("size") != size or finished.get("sha256") != sha256:
                raise SecurityError("Uploaded media digest or size does not match the source")
            return {"input_file": finished["input_file"], "size": size, "sha256": sha256, "upload_id": upload_id}
        except Exception:
            try:
                await self._media_request(peer, {"operation": "abort", "upload_id": upload_id})
            except Exception:
                pass
            raise

    async def download_media(
        self, peer: Peer, output_file: str, destination: str | Path,
        *, expected_size: int, expected_sha256: str, chunk_bytes: int = 2 * 1024 * 1024,
    ) -> Path:
        """Download a generated result and verify its full SHA-256 before publishing it."""
        import re
        if not re.fullmatch(r"[0-9a-f]{32}\.mp4", output_file):
            raise ValueError("Only generated RHEXORA MP4 outputs can be downloaded")
        if not (1 <= expected_size <= 512 * 1024 * 1024) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("Invalid expected media size or SHA-256")
        if not (1 <= chunk_bytes <= 2 * 1024 * 1024):
            raise ValueError("Download chunk size must be between 1 byte and 2 MiB")
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".partial")
        digest = hashlib.sha256()
        offset = 0
        try:
            with temporary.open("xb") as handle:
                while offset < expected_size:
                    response = await self._media_request(peer, {
                        "kind": "media.download", "output_file": output_file,
                        "offset": offset, "length": min(chunk_bytes, expected_size - offset),
                    })
                    if response.get("offset") != offset or response.get("size") != expected_size:
                        raise ProtocolError("Worker returned an unexpected media download range")
                    try:
                        block = base64.b64decode(response.get("data", ""), validate=True)
                    except (ValueError, base64.binascii.Error) as exc:
                        raise ProtocolError("Worker returned invalid media data") from exc
                    if not block or len(block) > chunk_bytes or offset + len(block) > expected_size:
                        raise ProtocolError("Worker returned an invalid media download chunk")
                    handle.write(block)
                    digest.update(block)
                    offset += len(block)
            actual = digest.hexdigest()
            if actual != expected_sha256:
                raise SecurityError("Downloaded media SHA-256 does not match the task result")
            os.replace(temporary, target)
            return target
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    async def distributed_sum(
        self,
        values: Iterable[int],
        peers: list[Peer],
        *,
        chunk_size: int = 10_000,
    ) -> dict[str, Any]:
        numbers = list(values)
        if not numbers:
            raise ValueError("At least one integer is required")
        if len(numbers) > MAX_DISTRIBUTED_ITEMS:
            raise ValueError(f"A single request is limited to {MAX_DISTRIBUTED_ITEMS} integers")
        if not peers:
            raise ConfigurationError("No trusted peers configured; add a peer invite first")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")

        # A chunk must fit every peer it may be assigned to.
        safe_chunk_size = min(chunk_size, min(peer.max_items for peer in peers))
        chunks = [numbers[i : i + safe_chunk_size] for i in range(0, len(numbers), safe_chunk_size)]
        semaphore = asyncio.Semaphore(self.max_in_flight)

        async def run_chunk(index: int, chunk: list[int]) -> dict[str, Any]:
            async with semaphore:
                return await self.execute(
                    peers[index % len(peers)], "math.sum_ints", {"values": chunk}
                )

        started = time.monotonic()
        results = await asyncio.gather(*(run_chunk(index, chunk) for index, chunk in enumerate(chunks)))
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        total = sum(item["result"]["sum"] for item in results)
        count = sum(item["result"]["count"] for item in results)
        return {
            "sum": total,
            "count": count,
            "chunks_completed": len(results),
            "workers": sorted({str(item["node_id"]) for item in results}),
            "elapsed_ms": elapsed_ms,
            "results": results,
        }

    async def distributed_prime_count(
        self,
        start: int,
        stop: int,
        peers: list[Peer],
        *,
        chunk_size: int = 5_000,
    ) -> dict[str, Any]:
        if isinstance(start, bool) or not isinstance(start, int):
            raise ValueError("start must be an integer")
        if isinstance(stop, bool) or not isinstance(stop, int):
            raise ValueError("stop must be an integer")
        if start < 0 or stop <= start:
            raise ValueError("Range must satisfy 0 <= start < stop")
        if stop - start > MAX_DISTRIBUTED_PRIME_RANGE:
            raise ValueError(f"Prime-count request is limited to {MAX_DISTRIBUTED_PRIME_RANGE} integers")
        if not peers:
            raise ConfigurationError("No trusted peers configured; add a peer invite first")
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")

        safe_chunk_size = min(chunk_size, min(min(peer.max_items * 2, 200_000) for peer in peers))
        ranges = [
            (left, min(left + safe_chunk_size, stop))
            for left in range(start, stop, safe_chunk_size)
        ]
        semaphore = asyncio.Semaphore(self.max_in_flight)

        async def run_range(index: int, left: int, right: int) -> dict[str, Any]:
            async with semaphore:
                return await self.execute(
                    peers[index % len(peers)],
                    "math.count_primes",
                    {"start": left, "stop": right},
                )

        started = time.monotonic()
        results = await asyncio.gather(
            *(run_range(index, left, right) for index, (left, right) in enumerate(ranges))
        )
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        return {
            "prime_count": sum(item["result"]["count"] for item in results),
            "range": {"start": start, "stop": stop, "stop_is_exclusive": True},
            "chunks_completed": len(results),
            "workers": sorted({str(item["node_id"]) for item in results}),
            "elapsed_ms": elapsed_ms,
            "results": results,
        }



def peers_from_data_dir(data_dir: str | Path) -> list[Peer]:
    return [Peer.from_dict(item) for item in load_peers(data_dir)]


def load_invite(path: str | Path) -> Peer:
    try:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Cannot read peer invite: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("invite_version") != 1:
        raise ConfigurationError("Peer invite must be a supported version 1 JSON object")
    return Peer.from_dict(raw)
