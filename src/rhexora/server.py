"""RHEXORA worker node: TLS TCP task endpoint plus optional LAN discovery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import socket
import ssl
import struct
import uuid
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .errors import ProtocolError
from .identity import certificate_fingerprint, load_node_config
from .protocol import DEFAULT_MAX_FRAME_BYTES, canonical_json, read_message, write_message
from .tasks import execute_task
from .store import TaskStore

LOG = logging.getLogger("rhexora.server")
DISCOVERY_GROUP = "239.255.77.77"
DISCOVERY_PORT = 47822


class _DiscoveryResponder(asyncio.DatagramProtocol):
    def __init__(self, server: "RhexoraServer") -> None:
        self.server = server
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.transport is None or len(data) > 4096:
            return
        try:
            packet = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(packet, dict) or packet.get("kind") != "discover":
            return
        response = {
            "kind": "peer",
            "protocol_version": PROTOCOL_VERSION,
            "node_id": self.server.config["node_id"],
            "tcp_port": self.server.bound_port,
            "tls_fingerprint": self.server.fingerprint,
            "capabilities": self.server.config["allowed_tasks"],
        }
        try:
            self.transport.sendto(canonical_json(response), addr)
        except (OSError, ProtocolError):
            LOG.debug("Unable to answer LAN discovery from %s", addr)


class RhexoraServer:
    def __init__(self, data_dir: str | Path) -> None:
        self.root, self.config = load_node_config(data_dir)
        self.fingerprint = certificate_fingerprint(self.root / self.config["tls_cert"])
        self._server: asyncio.AbstractServer | None = None
        self._discovery_transport: asyncio.DatagramTransport | None = None
        self._semaphore = asyncio.Semaphore(int(self.config.get("max_concurrent_tasks", 2)))
        self.task_store = TaskStore(self.root / "task-ledger.sqlite3")
        self._media_uploads: dict[str, dict[str, Any]] = {}
        self._media_lock = asyncio.Lock()
        if self.config.get("media_enabled"):
            self._cleanup_incomplete_media_uploads()
        self.bound_port = int(self.config.get("listen_port", 0))

    async def start(self) -> int:
        if self._server is not None:
            return self.bound_port
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_context.load_cert_chain(
            str(self.root / self.config["tls_cert"]),
            str(self.root / self.config["tls_key"]),
        )
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.get("listen_host", "0.0.0.0"),
            port=int(self.config.get("listen_port", 47821)),
            ssl=tls_context,
            limit=min(int(self.config.get("max_frame_bytes", DEFAULT_MAX_FRAME_BYTES)) + 4, 64 * 1024 * 1024),
        )
        sockets = self._server.sockets or []
        if sockets:
            self.bound_port = int(sockets[0].getsockname()[1])
        if self.config.get("discovery_enabled", True):
            try:
                await self._start_discovery()
            except OSError as exc:
                LOG.warning("LAN discovery unavailable (%s); direct peer connections remain enabled", exc)
        return self.bound_port

    async def _start_discovery(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", DISCOVERY_PORT))
            membership = struct.pack("4s4s", socket.inet_aton(DISCOVERY_GROUP), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.setblocking(False)
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _DiscoveryResponder(self), sock=sock
            )
            self._discovery_transport = transport  # type: ignore[assignment]
        except Exception:
            sock.close()
            raise

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request: dict[str, Any] | None = None
        try:
            # Do not let an idle or slow client occupy a task-execution slot.
            request = await asyncio.wait_for(
                read_message(
                    reader,
                    max_frame_bytes=int(self.config.get("max_frame_bytes", DEFAULT_MAX_FRAME_BYTES)),
                ),
                timeout=45,
            )
            async with self._semaphore:
                response = await self._process_request(request)
        except (ProtocolError, asyncio.TimeoutError) as exc:
            media_request = isinstance(request, dict) and request.get("kind") in {"media.upload", "media.download"}
            response = {
                "kind": "media_transfer" if media_request else "result",
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request.get("request_id") if isinstance(request, dict) else None,
                "node_id": self.config["node_id"],
                "ok": False,
                "error": str(exc),
            }
        except Exception:
            LOG.exception("Unhandled worker error")
            response = {
                "kind": "result",
                "protocol_version": PROTOCOL_VERSION,
                "ok": False,
                "error": "Internal worker error",
            }
        try:
            await write_message(
                writer,
                response,
                max_frame_bytes=int(self.config.get("max_frame_bytes", DEFAULT_MAX_FRAME_BYTES)),
            )
        except (ConnectionError, OSError, ProtocolError):
            LOG.debug("Could not return result to disconnected peer")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _process_request(self, request: dict[str, Any]) -> dict[str, Any]:
        kind = request.get("kind")
        if request.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError("Unsupported protocol version")
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not (1 <= len(request_id) <= 80):
            raise ProtocolError("Invalid request_id")
        token = request.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(
            token, str(self.config["auth_token"])
        ):
            return {
                "kind": "result" if kind == "execute" else "media_transfer",
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "node_id": self.config["node_id"],
                "ok": False,
                "error": "Authentication failed",
            }
        if kind == "media.upload":
            return await self._process_media_upload(request)
        if kind == "media.download":
            return await self._process_media_download(request)
        if kind != "execute":
            raise ProtocolError("Unsupported request kind")
        task = request.get("task")
        if not isinstance(task, dict):
            raise ProtocolError("Missing task object")
        task_type = task.get("type")
        payload = task.get("input")
        if not isinstance(task_type, str) or not isinstance(payload, dict):
            raise ProtocolError("Task must contain string type and object input")
        if task_type not in self.config.get("allowed_tasks", []):
            return {
                "kind": "result",
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "node_id": self.config["node_id"],
                "ok": False,
                "error": f"Task capability is disabled on this node: {task_type}",
            }

        task_hash = hashlib.sha256(canonical_json(task)).hexdigest()
        claim = self.task_store.claim(request_id, task_hash)
        if claim["state"] == "conflict":
            return {
                "kind": "result",
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "node_id": self.config["node_id"],
                "ok": False,
                "error": "This request_id was already used for a different task",
            }
        if claim["state"] in {"completed", "failed"}:
            # Return the exact previously stored outcome for an idempotent retry.
            return claim["response"]
        if claim["state"] == "running":
            return {
                "kind": "result",
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "node_id": self.config["node_id"],
                "ok": False,
                "retryable": True,
                "error": "Task with this request_id is still running",
            }

        try:
            result = await asyncio.to_thread(
                execute_task,
                task_type,
                payload,
                max_items=int(self.config.get("max_items", 100_000)),
                media_root=str(self.root / "shared-media") if self.config.get("media_enabled") else None,
            )
        except Exception as exc:
            response = {
                "kind": "result",
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "node_id": self.config["node_id"],
                "ok": False,
                "task_hash": task_hash,
                "error": str(exc),
            }
            self.task_store.save_response(
                request_id, task_hash=task_hash, response=response, status="failed"
            )
            return response

        response = {
            "kind": "result",
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request_id,
            "node_id": self.config["node_id"],
            "ok": True,
            "task_hash": task_hash,
            "result": result,
            "result_hash": hashlib.sha256(canonical_json(result)).hexdigest(),
        }
        self.task_store.save_response(
            request_id, task_hash=task_hash, response=response, status="completed"
        )
        return response

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _cleanup_incomplete_media_uploads(self) -> None:
        incoming = self.root / "shared-media" / ".incoming"
        try:
            incoming.mkdir(parents=True, exist_ok=True)
            for item in incoming.glob("*.part"):
                try:
                    if item.is_file() and not item.is_symlink():
                        item.unlink(missing_ok=True)
                except OSError:
                    LOG.warning("Could not remove a stale partial media upload: %s", item.name)
        except OSError:
            LOG.warning("Could not initialize temporary media upload directory")

    def _media_response(self, request: dict[str, Any], **fields: Any) -> dict[str, Any]:
        response = {
            "kind": "media_transfer",
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request.get("request_id"),
            "node_id": self.config["node_id"],
            "ok": True,
        }
        response.update(fields)
        return response

    async def _process_media_upload(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.config.get("media_enabled"):
            return self._media_response(request, ok=False, error="Media uploads are disabled on this worker")
        operation = request.get("operation")
        upload_id = request.get("upload_id")
        if not isinstance(upload_id, str) or not re.fullmatch(r"[0-9a-f]{32}", upload_id):
            raise ProtocolError("Invalid media upload ID")
        media_root = self.root / "shared-media"
        incoming = media_root / ".incoming"
        uploads = media_root / "uploads"
        if media_root.is_symlink():
            raise ProtocolError("Worker shared-media directory must not be a symlink")
        incoming.mkdir(parents=True, exist_ok=True)
        uploads.mkdir(parents=True, exist_ok=True)

        async with self._media_lock:
            if operation == "start":
                size = request.get("size")
                digest = request.get("sha256")
                if isinstance(size, bool) or not isinstance(size, int) or not (1 <= size <= 512 * 1024 * 1024):
                    raise ProtocolError("Media upload size is outside the 1..512 MiB limit")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ProtocolError("Invalid media upload SHA-256")
                if len(self._media_uploads) >= 8:
                    raise ProtocolError("This worker already has the maximum number of active media uploads")
                if incoming.is_symlink() or uploads.is_symlink():
                    raise ProtocolError("Media storage directories must not be symlinks")
                if upload_id in self._media_uploads:
                    raise ProtocolError("Media upload ID already exists")
                temp_path = incoming / f"{upload_id}.part"
                try:
                    with temp_path.open("xb"):
                        pass
                except FileExistsError as exc:
                    raise ProtocolError("Media upload ID collides with an existing upload") from exc
                self._media_uploads[upload_id] = {
                    "path": temp_path,
                    "size": size,
                    "sha256": digest,
                    "written": 0,
                    "sequence": 0,
                }
                return self._media_response(request, operation="start", upload_id=upload_id, chunk_bytes=2 * 1024 * 1024)

            state = self._media_uploads.get(upload_id)
            if state is None:
                raise ProtocolError("Unknown or expired media upload")
            if operation == "chunk":
                sequence = request.get("sequence")
                encoded = request.get("data")
                if isinstance(sequence, bool) or sequence != state["sequence"]:
                    raise ProtocolError("Media chunk sequence is not the next expected value")
                if not isinstance(encoded, str) or len(encoded) > 2_800_000:
                    raise ProtocolError("Media chunk exceeds the encoded size limit")
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, base64.binascii.Error) as exc:
                    raise ProtocolError("Media chunk is not valid base64") from exc
                if not data or len(data) > 2 * 1024 * 1024 or state["written"] + len(data) > state["size"]:
                    raise ProtocolError("Media chunk has an invalid size")
                with state["path"].open("ab") as handle:
                    handle.write(data)
                state["written"] += len(data)
                state["sequence"] += 1
                return self._media_response(request, operation="chunk", upload_id=upload_id, sequence=sequence, written=state["written"])

            if operation == "finish":
                if state["written"] != state["size"]:
                    raise ProtocolError("Media upload is incomplete")
                digest = await asyncio.to_thread(self._sha256_file, state["path"])
                if not hmac.compare_digest(digest, state["sha256"]):
                    state["path"].unlink(missing_ok=True)
                    self._media_uploads.pop(upload_id, None)
                    raise ProtocolError("Media upload SHA-256 mismatch")
                final_path = uploads / f"{upload_id}.mp4"
                state["path"].replace(final_path)
                self._media_uploads.pop(upload_id, None)
                return self._media_response(
                    request, operation="finish", upload_id=upload_id,
                    input_file=f"uploads/{upload_id}.mp4", size=state["size"], sha256=digest,
                )

            if operation == "abort":
                state["path"].unlink(missing_ok=True)
                self._media_uploads.pop(upload_id, None)
                return self._media_response(request, operation="abort", upload_id=upload_id)
        raise ProtocolError("Unsupported media upload operation")

    async def _process_media_download(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.config.get("media_enabled"):
            return self._media_response(request, ok=False, error="Media downloads are disabled on this worker")
        filename = request.get("output_file")
        if not isinstance(filename, str) or not re.fullmatch(r"[0-9a-f]{32}\.mp4", filename):
            raise ProtocolError("Only generated processed-media output names can be downloaded")
        output_root = self.root / "processed-media"
        path = output_root / filename
        try:
            if output_root.is_symlink() or path.is_symlink() or not path.is_file():
                raise ProtocolError("Processed media file does not exist")
            path.resolve(strict=True).relative_to(output_root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise ProtocolError("Processed media file is unavailable") from exc
        size = path.stat().st_size
        offset = request.get("offset")
        length = request.get("length", 2 * 1024 * 1024)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or offset > size:
            raise ProtocolError("Invalid media download offset")
        if isinstance(length, bool) or not isinstance(length, int) or not (1 <= length <= 2 * 1024 * 1024):
            raise ProtocolError("Invalid media download chunk length")
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(min(length, size - offset))
        return self._media_response(
            request, output_file=filename, offset=offset, size=size,
            data=base64.b64encode(data).decode("ascii"),
            eof=offset + len(data) >= size,
        )

    async def close(self) -> None:
        if self._discovery_transport is not None:
            self._discovery_transport.close()
            self._discovery_transport = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()
