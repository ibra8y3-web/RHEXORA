"""WebRTC ICE data-channel tunnel for direct peer connectivity.

The tunnel transports the existing RHEXORA TLS byte stream over an ordered,
reliable WebRTC data channel. TLS is therefore still terminated only by the
trusted worker, and clients continue to pin the worker's certificate.

Install the optional extra with ``pip install 'rhexora[ice]'``. A TURN server
is required for networks where STUN-based direct connectivity cannot succeed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from .errors import RhexoraError
from urllib.parse import quote, urlsplit, urlunsplit

LOG = logging.getLogger("rhexora.ice")
MAX_SIGNAL_MESSAGE = 64 * 1024
MAX_SIGNAL_MESSAGES_PER_SESSION = 256
MAX_SIGNAL_ROOMS = 1024
DATA_CHANNEL_CHUNK = 16 * 1024
DATA_CHANNEL_HIGH_WATER = 512 * 1024
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _aiohttp_dependencies() -> tuple[Any, Any, Any]:
    try:
        from aiohttp import ClientSession, WSMsgType, web
    except ImportError as exc:  # pragma: no cover - exercised in environments without extra
        raise RhexoraError("ICE signaling requires aiohttp: pip install 'rhexora[ice]'") from exc
    return ClientSession, WSMsgType, web


def _aiortc_dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
    except ImportError as exc:  # pragma: no cover - exercised in environments without extra
        raise RhexoraError("ICE data channels require aiortc: pip install 'rhexora[ice]'") from exc
    return RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription


def _optional_dependencies() -> tuple[Any, Any, Any, Any]:
    ClientSession, WSMsgType, web = _aiohttp_dependencies()
    return ClientSession, WSMsgType, web, _aiortc_dependencies()


def _signal_ws_url(base_url: str, room: str, role: str) -> str:
    if not ROOM_RE.fullmatch(room):
        raise ValueError("ICE room must be a random URL-safe token of 32..128 characters")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("ICE signaling URL must be an absolute http(s) or ws(s) URL")
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path.rstrip("/") + "/signal/" + quote(room, safe="")
    return urlunsplit((scheme, parsed.netloc, path, f"role={role}", ""))


def ice_server_configuration(
    *, stun_urls: list[str] | None = None, turn_url: str | None = None,
    turn_username: str | None = None, turn_credential: str | None = None,
) -> Any:
    """Build aiortc's ICE config. TURN credentials are never logged or returned."""
    RTCConfiguration, RTCIceServer, _RTCPeerConnection, _RTCSessionDescription = _aiortc_dependencies()
    urls = ["stun:stun.l.google.com:19302"] if stun_urls is None else stun_urls
    servers = [RTCIceServer(urls=url) for url in urls if url]
    if turn_url:
        if not turn_username or not turn_credential:
            raise ValueError("TURN URL requires both TURN username and credential")
        servers.append(RTCIceServer(urls=turn_url, username=turn_username, credential=turn_credential))
    return RTCConfiguration(iceServers=servers)


class IceSignalBroker:
    """Minimal, ephemeral SDP rendezvous server. The 256-bit room token is a bearer capability."""

    def __init__(self, *, room_idle_timeout: float = 180.0, max_rooms: int = MAX_SIGNAL_ROOMS) -> None:
        if room_idle_timeout < 10 or max_rooms < 1:
            raise ValueError("room_idle_timeout must be at least 10 seconds and max_rooms must be positive")
        self.room_idle_timeout = room_idle_timeout
        self.max_rooms = max_rooms
        self._rooms: dict[str, dict[str, Any]] = {}
        self._runner: Any = None
        self._site: Any = None
        self.bound_port: int | None = None

    async def start(self, host: str = "0.0.0.0", port: int = 47826) -> int:
        _ClientSession, _WSMsgType, web = _aiohttp_dependencies()
        app = web.Application(client_max_size=MAX_SIGNAL_MESSAGE)
        app.router.add_get("/health", self._health)
        app.router.add_get("/signal/{room}", self._handle_signal)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        sockets = getattr(self._site, "_server", None).sockets
        self.bound_port = int(sockets[0].getsockname()[1])
        return self.bound_port

    async def _health(self, _request: Any) -> Any:
        _ClientSession, _WSMsgType, web = _aiohttp_dependencies()
        return web.json_response({"service": "rhexora-ice-signaling", "status": "ok", "rooms": len(self._rooms)})

    async def _handle_signal(self, request: Any) -> Any:
        _ClientSession, WSMsgType, web = _aiohttp_dependencies()
        room = request.match_info.get("room", "")
        role = request.query.get("role", "")
        if not ROOM_RE.fullmatch(room) or role not in {"client", "worker"}:
            raise web.HTTPBadRequest(text="Invalid room token or role")
        now = asyncio.get_running_loop().time()
        # Drop abandoned empty rooms before admitting new capabilities.
        for old_room, old_state in list(self._rooms.items()):
            if (old_state["client"] is None and old_state["worker"] is None
                    and now - old_state["last"] > self.room_idle_timeout):
                self._rooms.pop(old_room, None)
        state = self._rooms.get(room)
        if state is None:
            if len(self._rooms) >= self.max_rooms:
                raise web.HTTPServiceUnavailable(text="ICE signaling room capacity reached")
            state = {"client": None, "worker": None, "queues": {"client": [], "worker": []}, "last": now}
            self._rooms[room] = state
        ws = web.WebSocketResponse(max_msg_size=MAX_SIGNAL_MESSAGE, heartbeat=25, autoping=True)
        await ws.prepare(request)
        if state[role] is not None and not state[role].closed:
            await ws.close(code=4001, message=b"role already connected")
            return ws
        state[role] = ws
        state["last"] = asyncio.get_running_loop().time()
        other_role = "worker" if role == "client" else "client"
        other = state[other_role]
        if state["queues"][role]:
            for queued in state["queues"][role]:
                await ws.send_str(queued)
            state["queues"][role].clear()
        forwarded_count = 0
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    forwarded_count += 1
                    if forwarded_count > MAX_SIGNAL_MESSAGES_PER_SESSION:
                        await ws.close(code=1008, message=b"signaling message limit reached")
                        break
                    if len(message.data.encode("utf-8")) > MAX_SIGNAL_MESSAGE:
                        await ws.close(code=1009, message=b"signal too large")
                        break
                    try:
                        data = json.loads(message.data)
                    except json.JSONDecodeError:
                        await ws.close(code=1003, message=b"invalid JSON")
                        break
                    if not isinstance(data, dict) or data.get("type") not in {"offer", "answer", "candidate", "close"}:
                        await ws.close(code=1003, message=b"invalid signaling message")
                        break
                    target = state[other_role]
                    if target is not None and not target.closed:
                        await target.send_str(message.data)
                    else:
                        queue = state["queues"][other_role]
                        if len(queue) >= 8:
                            await ws.close(code=1013, message=b"peer not ready")
                            break
                        queue.append(message.data)
                    state["last"] = asyncio.get_running_loop().time()
                elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED}:
                    break
        finally:
            if state.get(role) is ws:
                state[role] = None
            other = state.get(other_role)
            if other is not None and not other.closed:
                with suppress(Exception):
                    await other.send_json({"type": "close", "reason": "peer disconnected"})
            if state["client"] is None and state["worker"] is None:
                self._rooms.pop(room, None)
        return ws

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            self.bound_port = None
        self._rooms.clear()


@dataclass
class IceTunnel:
    """Local TCP listener bridged to a remote worker through WebRTC DataChannel."""

    pc: Any
    channel: Any
    session: Any
    websocket: Any
    server: asyncio.AbstractServer
    port: int

    async def close(self) -> None:
        self.server.close()
        await self.server.wait_closed()
        with suppress(Exception):
            await self.websocket.send_json({"type": "close"})
        with suppress(Exception):
            await self.websocket.close()
        with suppress(Exception):
            await self.pc.close()
        with suppress(Exception):
            await self.session.close()


async def open_ice_tunnel(
    signaling_url: str,
    room: str,
    *,
    timeout: float = 30.0,
    stun_urls: list[str] | None = None,
    turn_url: str | None = None,
    turn_username: str | None = None,
    turn_credential: str | None = None,
) -> IceTunnel:
    """Open a WebRTC peer connection and expose its data channel as local TCP."""
    ClientSession, _WSMsgType, _web, rtc = _optional_dependencies()
    RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription = rtc
    config = ice_server_configuration(stun_urls=stun_urls, turn_url=turn_url, turn_username=turn_username, turn_credential=turn_credential)
    session = ClientSession()
    pc = RTCPeerConnection(configuration=config)
    websocket = None
    channel = pc.createDataChannel("rhexora-tls", ordered=True)
    opened = asyncio.Event()
    closed = asyncio.Event()
    channel.on("open", lambda: opened.set())
    channel.on("close", lambda: closed.set())
    local_server: asyncio.AbstractServer | None = None

    def bridge_local(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        @channel.on("message")
        def on_message(message: Any) -> None:
            if isinstance(message, bytes) and not writer.is_closing():
                writer.write(message)
                asyncio.create_task(writer.drain())

        async def run_bridge() -> None:
            try:
                while True:
                    block = await reader.read(DATA_CHANNEL_CHUNK)
                    if not block:
                        break
                    while channel.readyState == "open" and channel.bufferedAmount > DATA_CHANNEL_HIGH_WATER:
                        await asyncio.sleep(0.01)
                    if channel.readyState != "open":
                        break
                    channel.send(block)
            finally:
                with suppress(Exception):
                    channel.close()
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()
        asyncio.create_task(run_bridge())

    try:
        websocket = await session.ws_connect(_signal_ws_url(signaling_url, room, "client"), heartbeat=20, max_msg_size=MAX_SIGNAL_MESSAGE)
        local_server = await asyncio.start_server(bridge_local, "127.0.0.1", 0)
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await websocket.send_json({"type": "offer", "sdp": pc.localDescription.sdp})
        async def establish() -> None:
            async for message in websocket:
                if message.type == _WSMsgType.TEXT:
                    data = json.loads(message.data)
                    if data.get("type") == "answer":
                        await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="answer"))
                        break
                    if data.get("type") == "close":
                        raise ConnectionError("ICE worker closed signaling session")
                elif message.type in {_WSMsgType.ERROR, _WSMsgType.CLOSED}:
                    raise ConnectionError("ICE signaling connection closed before answer")
            else:
                raise ConnectionError("ICE signaling ended before SDP answer")
            await opened.wait()
        await asyncio.wait_for(establish(), timeout=timeout)
        port = int(local_server.sockets[0].getsockname()[1])
        return IceTunnel(pc=pc, channel=channel, session=session, websocket=websocket, server=local_server, port=port)
    except Exception:
        if local_server:
            local_server.close()
            await local_server.wait_closed()
        with suppress(Exception):
            await pc.close()
        if websocket is not None:
            with suppress(Exception):
                await websocket.close()
        await session.close()
        raise


async def run_ice_worker(
    data_dir: str,
    signaling_url: str,
    room: str,
    *,
    reconnect_seconds: float = 3.0,
    stun_urls: list[str] | None = None,
    turn_url: str | None = None,
    turn_username: str | None = None,
    turn_credential: str | None = None,
    turn_shared_secret: str | None = None,
    turn_ttl_seconds: int = 3600,
    local_host: str = "127.0.0.1",
    local_port: int | None = None,
) -> None:
    """Keep a worker registered and bridge ICE DataChannels to its local TLS listener."""
    ClientSession, WSMsgType, _web, rtc = _optional_dependencies()
    RTCConfiguration, _RTCIceServer, RTCPeerConnection, RTCSessionDescription = rtc
    from .identity import load_node_config

    root, config_file = load_node_config(data_dir)
    destination_port = int(local_port if local_port is not None else config_file.get("listen_port", 47821))
    if not 1 <= destination_port <= 65535:
        raise ValueError("Local RHEXORA worker port must be valid")
    if turn_shared_secret and len(turn_shared_secret) < 32:
        raise ValueError("TURN shared secret must contain at least 32 characters")
    websocket_url = _signal_ws_url(signaling_url, room, "worker")

    while True:
        # coturn REST credentials expire. Mint a fresh username/password on every
        # reconnect rather than reusing an expired credential for a long-running worker.
        session_turn_username = turn_username
        session_turn_credential = turn_credential
        if turn_shared_secret:
            session_turn_username, session_turn_credential = create_turn_credentials(
                turn_shared_secret, ttl_seconds=turn_ttl_seconds
            )
        config = ice_server_configuration(
            stun_urls=stun_urls,
            turn_url=turn_url,
            turn_username=session_turn_username,
            turn_credential=session_turn_credential,
        )
        session = ClientSession()
        pc = RTCPeerConnection(configuration=config)
        ws = None
        channel_closed = asyncio.Event()
        bridge_tasks: set[asyncio.Task[Any]] = set()

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if pc.connectionState in {"failed", "closed"}:
                channel_closed.set()

        @pc.on("datachannel")
        def on_datachannel(channel: Any) -> None:
            pending_incoming: list[bytes] = []
            local_writer_holder: dict[str, asyncio.StreamWriter] = {}

            @channel.on("message")
            def on_message(message: Any) -> None:
                if not isinstance(message, bytes):
                    return
                local_writer = local_writer_holder.get("writer")
                if local_writer is not None and not local_writer.is_closing():
                    local_writer.write(message)
                    asyncio.create_task(local_writer.drain())
                elif len(pending_incoming) < 32:
                    pending_incoming.append(message)
                else:
                    channel.close()

            @channel.on("close")
            def on_close() -> None:
                channel_closed.set()

            async def bridge() -> None:
                try:
                    local_reader, local_writer = await asyncio.open_connection(local_host, destination_port)
                except OSError as exc:
                    LOG.warning("ICE DataChannel could not connect to local TLS worker: %s", exc)
                    channel_closed.set()
                    with suppress(Exception):
                        channel.close()
                    return
                local_writer_holder["writer"] = local_writer
                for pending in pending_incoming:
                    local_writer.write(pending)
                pending_incoming.clear()
                await local_writer.drain()
                async def socket_to_channel() -> None:
                    try:
                        while True:
                            chunk = await local_reader.read(DATA_CHANNEL_CHUNK)
                            if not chunk:
                                break
                            while channel.readyState == "open" and channel.bufferedAmount > DATA_CHANNEL_HIGH_WATER:
                                await asyncio.sleep(0.01)
                            if channel.readyState != "open":
                                break
                            channel.send(chunk)
                    finally:
                        with suppress(Exception):
                            channel.close()
                try:
                    await socket_to_channel()
                finally:
                    local_writer_holder.pop("writer", None)
                    local_writer.close()
                    with suppress(Exception):
                        await local_writer.wait_closed()
                    channel_closed.set()
            task = asyncio.create_task(bridge())
            bridge_tasks.add(task)
            task.add_done_callback(bridge_tasks.discard)

        try:
            ws = await session.ws_connect(websocket_url, heartbeat=20, max_msg_size=MAX_SIGNAL_MESSAGE)
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    data = json.loads(message.data)
                    if data.get("type") == "offer":
                        await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="offer"))
                        answer = await pc.createAnswer()
                        await pc.setLocalDescription(answer)
                        await ws.send_json({"type": "answer", "sdp": pc.localDescription.sdp})
                        break
                    if data.get("type") == "close":
                        break
                elif message.type in {WSMsgType.ERROR, WSMsgType.CLOSED}:
                    break
            if pc.remoteDescription is not None:
                try:
                    await asyncio.wait_for(channel_closed.wait(), timeout=3600)
                except asyncio.TimeoutError:
                    LOG.warning("ICE worker session expired after one hour")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("ICE worker session unavailable: %s", exc)
        finally:
            for task in list(bridge_tasks):
                task.cancel()
            if bridge_tasks:
                await asyncio.gather(*bridge_tasks, return_exceptions=True)
            if ws is not None:
                with suppress(Exception):
                    await ws.close()
            with suppress(Exception):
                await pc.close()
            await session.close()
        await asyncio.sleep(reconnect_seconds)


def create_turn_credentials(shared_secret: str, *, ttl_seconds: int = 3600, user_id: str | None = None, now: int | None = None) -> tuple[str, str]:
    """Create coturn REST-auth credentials (expiry username + HMAC-SHA1 password)."""
    if len(shared_secret) < 32:
        raise ValueError("TURN shared secret must contain at least 32 characters")
    if not 300 <= ttl_seconds <= 86_400:
        raise ValueError("TURN credential TTL must be between 300 and 86400 seconds")
    suffix = user_id or secrets.token_hex(8)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", suffix):
        raise ValueError("TURN user ID contains unsupported characters")
    expiry = (int(time.time()) if now is None else int(now)) + ttl_seconds
    username = f"{expiry}:{suffix}"
    digest = hmac.new(shared_secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    return username, base64.b64encode(digest).decode("ascii")


def ice_settings_from_environment() -> dict[str, Any]:
    """Return runtime ICE settings; TURN credentials are generated per process and never logged."""
    stun = [item.strip() for item in os.getenv("RHEXORA_ICE_STUN", "stun:stun.l.google.com:19302").split(",") if item.strip()]
    turn_url = os.getenv("RHEXORA_ICE_TURN_URL") or None
    shared_secret = os.getenv("RHEXORA_ICE_TURN_SHARED_SECRET") or None
    if shared_secret:
        ttl = int(os.getenv("RHEXORA_ICE_TURN_TTL_SECONDS", "3600"))
        turn_username, turn_credential = create_turn_credentials(shared_secret, ttl_seconds=ttl)
    else:
        turn_username = os.getenv("RHEXORA_ICE_TURN_USERNAME") or None
        turn_credential = os.getenv("RHEXORA_ICE_TURN_CREDENTIAL") or None
        if bool(turn_username) != bool(turn_credential):
            raise ValueError("Static TURN configuration requires both username and credential")
    if turn_url and not (shared_secret or (turn_username and turn_credential)):
        # TURN is optional, but partially configured authentication must fail closed.
        raise ValueError("TURN URL requires a shared secret or both static TURN credentials")
    return {"stun_urls": stun, "turn_url": turn_url, "turn_username": turn_username, "turn_credential": turn_credential}
