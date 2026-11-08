"""Command-line interface for RHEXORA nodes and trusted peers."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from .client import RhexoraClient, Peer, load_invite, peers_from_data_dir
from .discovery import discover_peers
from .errors import RhexoraError
from .identity import (
    certificate_fingerprint,
    initialize_node,
    load_node_config,
    load_peers,
    save_peers,
)
from .server import RhexoraServer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rhexora",
        description="RHEXORA — permissioned peer-to-peer capability network",
    )
    parser.add_argument("--version", action="version", version="RHEXORA 0.4.0a1")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a new node identity and TLS credentials")
    init.add_argument("--data-dir", default="./rhexora-node")
    init.add_argument("--host", default="0.0.0.0", help="Local bind address")
    init.add_argument("--port", type=int, default=47821)
    init.add_argument("--max-items", type=int, default=100_000)
    init.add_argument("--max-concurrent-tasks", type=int, default=2)
    init.add_argument("--enable-video", action="store_true", help="Allow FFmpeg jobs on files placed in shared-media/")

    run = sub.add_parser("run", help="Start this node as a task worker")
    run.add_argument("--data-dir", default="./rhexora-node")
    run.add_argument("--verbose", action="store_true")

    fingerprint = sub.add_parser("fingerprint", help="Print this node's TLS certificate fingerprint")
    fingerprint.add_argument("--data-dir", default="./rhexora-node")

    invite = sub.add_parser("invite", help="Create a confidential invite for a trusted peer")
    invite.add_argument("--data-dir", default="./rhexora-node")
    invite.add_argument("--host", required=True, help="Address reachable by the other peer")
    invite.add_argument("--output", default="rhexora.invite.json")
    invite.add_argument("--name", default="peer")
    invite.add_argument("--relay-host", default="", help="Optional public self-hosted relay hostname")
    invite.add_argument("--relay-port", type=int, default=0, help="Optional relay TCP port")
    invite.add_argument("--ice-signal-url", default="", help="Optional public WebRTC signaling base URL")
    invite.add_argument("--ice-room", default="", help="Random 256-bit ICE room token shared with the worker")

    peer = sub.add_parser("peer", help="Manage trusted peers")
    peer_sub = peer.add_subparsers(dest="peer_command", required=True)
    add = peer_sub.add_parser("add", help="Trust a peer using its private invite file")
    add.add_argument("invite_file")
    add.add_argument("--data-dir", default="./rhexora-node")
    listing = peer_sub.add_parser("list", help="List trusted peers")
    listing.add_argument("--data-dir", default="./rhexora-node")
    remove = peer_sub.add_parser("remove", help="Remove a trusted peer by name")
    remove.add_argument("name")
    remove.add_argument("--data-dir", default="./rhexora-node")

    discover = sub.add_parser("discover", help="Find RHEXORA nodes on the local network")
    discover.add_argument("--timeout", type=float, default=3.0)

    nat = sub.add_parser("nat-check", help="Discover the UDP-mapped address using STUN (not a reachability guarantee)")
    nat.add_argument("--server", default="stun.l.google.com")
    nat.add_argument("--port", type=int, default=19302)
    nat.add_argument("--timeout", type=float, default=3.0)

    media = sub.add_parser("media-capabilities", help="Show local FFmpeg video encoder capabilities")

    dht = sub.add_parser("dht-run", help="Start a signed-record DHT node")
    dht.add_argument("--data-dir", default="./rhexora-node")
    dht.add_argument("--bind-host", default="0.0.0.0")
    dht.add_argument("--advertise-host", required=True, help="Reachable public/LAN IP or DNS name")
    dht.add_argument("--port", type=int, default=47823)
    dht.add_argument("--bootstrap", action="append", default=[], metavar="HOST:PORT")
    dht.add_argument("--bootstrap-file", default="", help="JSON file containing a list of public HOST:PORT bootstrap endpoints")
    dht.add_argument("--allow-private-endpoints", action="store_true", help="Permit private/LAN DHT endpoints (trusted test or private overlay only)")

    dht_peers = sub.add_parser("dht-peers", help="List unexpired signed peer records stored by the DHT")
    dht_peers.add_argument("--data-dir", default="./rhexora-node")

    relay_credentials = sub.add_parser("relay-credentials", help="Create a private credential map for a self-hosted relay")
    relay_credentials.add_argument("--output", default="./relay-credentials.json")
    relay_credentials.add_argument("--node-dir", action="append", required=True, help="Worker data directory; repeat for each worker")

    relay_serve = sub.add_parser("relay-serve", help="Run a self-hosted opaque TCP relay")
    relay_serve.add_argument("--credentials", required=True, help="Private JSON mapping node_id to independent relay_token")
    relay_serve.add_argument("--host", default="0.0.0.0")
    relay_serve.add_argument("--port", type=int, default=47825)
    relay_serve.add_argument("--worker-wait-timeout", type=float, default=30.0)

    relay_worker = sub.add_parser("relay-worker", help="Maintain an outbound relay tunnel for a local TLS worker")
    relay_worker.add_argument("--data-dir", default="./rhexora-node")
    relay_worker.add_argument("--relay-host", required=True)
    relay_worker.add_argument("--relay-port", type=int, default=47825)
    relay_worker.add_argument("--local-host", default="127.0.0.1")
    relay_worker.add_argument("--local-port", type=int, default=0)
    relay_worker.add_argument("--reconnect-seconds", type=float, default=2.0)

    ice_signal = sub.add_parser("ice-signal-serve", help="Run an ephemeral WebRTC ICE signaling broker (requires rhexora[ice])")
    ice_signal.add_argument("--host", default="0.0.0.0")
    ice_signal.add_argument("--port", type=int, default=47826)

    ice_worker = sub.add_parser("ice-worker", help="Accept ICE DataChannel tunnels and bridge them to a TLS worker")
    ice_worker.add_argument("--data-dir", default="./rhexora-node")
    ice_worker.add_argument("--signal-url", required=True)
    ice_worker.add_argument("--room", required=True)
    ice_worker.add_argument("--reconnect-seconds", type=float, default=3.0)

    ice_room = sub.add_parser("ice-room", help="Generate a cryptographically random ICE room bearer token")

    submit = sub.add_parser("submit", help="Submit a task to trusted peers")
    submit_sub = submit.add_subparsers(dest="task_command", required=True)
    sum_parser = submit_sub.add_parser("sum", help="Distribute integer-sum chunks across peers")
    sum_parser.add_argument("--data-dir", default="./rhexora-node")
    source = sum_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--values", help="Comma-separated integers")
    source.add_argument("--file", help="UTF-8 text file containing one integer per line")
    sum_parser.add_argument("--chunk-size", type=int, default=10_000)

    primes_parser = submit_sub.add_parser("primes", help="Distribute a prime-count range across peers")
    primes_parser.add_argument("--data-dir", default="./rhexora-node")
    primes_parser.add_argument("--start", type=int, required=True)
    primes_parser.add_argument("--stop", type=int, required=True, help="Exclusive upper bound")
    primes_parser.add_argument("--chunk-size", type=int, default=5_000)

    text_parser = submit_sub.add_parser("text", help="Analyze a UTF-8 text file on a peer")
    text_parser.add_argument("file")
    text_parser.add_argument("--peer", required=True, help="Trusted peer name")
    text_parser.add_argument("--data-dir", default="./rhexora-node")

    video_parser = submit_sub.add_parser("video", help="Transcode a file already shared on a worker")
    video_parser.add_argument("--data-dir", default="./rhexora-node")
    video_parser.add_argument("--peer", required=True, help="Trusted peer name")
    video_parser.add_argument("--input-file", required=True, help="Relative path inside worker shared-media/")
    video_parser.add_argument("--encoder", choices=["auto", "libx264", "h264_nvenc", "h264_vaapi", "h264_qsv", "h264_amf", "h264_videotoolbox"], default="auto")
    video_parser.add_argument("--no-cpu-fallback", action="store_true")
    video_parser.add_argument("--timeout", type=float, default=900.0, help="Maximum wait for video processing, in seconds")

    distributed_video = submit_sub.add_parser(
        "video-distribute", help="Upload, split, transcode on multiple trusted workers and assemble a video"
    )
    distributed_video.add_argument("--data-dir", default="./rhexora-node")
    distributed_video.add_argument("--input", required=True, help="Local source video path")
    distributed_video.add_argument("--output", required=True, help="Local output MP4 path")
    distributed_video.add_argument("--segment-seconds", type=int, default=30)
    distributed_video.add_argument("--encoder", choices=["libx264", "h264_nvenc", "h264_vaapi", "h264_qsv", "h264_amf", "h264_videotoolbox"], default="libx264")
    distributed_video.add_argument("--timeout", type=float, default=1800.0)
    distributed_video.add_argument("--max-in-flight", type=int, default=4)

    return parser


def _load_peers_by_name(data_dir: str, name: str) -> Peer:
    matches = [peer for peer in peers_from_data_dir(data_dir) if peer.name == name]
    if not matches:
        raise RhexoraError(f"No trusted peer named {name!r}")
    if len(matches) > 1:
        raise RhexoraError(f"More than one trusted peer is named {name!r}; use unique names")
    return matches[0]


def _parse_values(args: argparse.Namespace) -> list[int]:
    if args.values is not None:
        raw_values = args.values.split(",")
        if not raw_values or any(not part.strip() for part in raw_values):
            raise RhexoraError("--values must be a comma-separated list of integers")
        try:
            return [int(part.strip(), 10) for part in raw_values]
        except ValueError as exc:
            raise RhexoraError("--values contains a non-integer value") from exc
    path = Path(args.file).expanduser()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RhexoraError(f"Cannot read input file: {exc}") from exc
    values: list[int] = []
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            values.append(int(stripped, 10))
        except ValueError as exc:
            raise RhexoraError(f"Invalid integer on line {line_no}") from exc
    if not values:
        raise RhexoraError("Input file contains no integers")
    return values


async def _run_dht(data_dir: str, bind_host: str, advertise_host: str, port: int, bootstrap: list[str], allow_private_endpoints: bool = False) -> None:
    from .dht import DHTNode
    endpoints: list[tuple[str, int]] = []
    for item in bootstrap:
        host, separator, raw_port = item.rpartition(":")
        if not separator or not host or not raw_port.isdigit() or not (1 <= int(raw_port) <= 65535):
            raise RhexoraError(f"Invalid bootstrap endpoint: {item!r}; expected HOST:PORT")
        endpoints.append((host, int(raw_port)))
    node = DHTNode(data_dir, advertise_host, port=port, bind_host=bind_host, allow_private_endpoints=allow_private_endpoints)
    host, bound_port = await node.start()
    joined = await node.bootstrap(endpoints) if endpoints else 0
    print(f"RHEXORA DHT node {node.local_id} listening on {host}:{bound_port}")
    print(f"Advertised endpoint: {advertise_host}:{port}; bootstrap peers joined: {joined}")
    print("DHT records are signed discovery hints only; they do not grant task-execution trust.")
    try:
        await asyncio.Future()
    finally:
        await node.close()


async def _run_relay_server(credentials_file: str, host: str, port: int, worker_wait_timeout: float) -> None:
    from .relay import RhexoraRelayServer, load_relay_credentials
    relay = RhexoraRelayServer(load_relay_credentials(credentials_file), worker_wait_timeout=worker_wait_timeout)
    bound = await relay.start(host, port)
    print(f"RHEXORA opaque relay listening on {host}:{bound}")
    print("Relay pairs authenticated peers and forwards end-to-end TLS bytes; it does not decrypt task payloads.")
    try:
        await asyncio.Future()
    finally:
        await relay.close()


async def _run_relay_worker(data_dir: str, relay_host: str, relay_port: int, local_host: str, local_port: int, reconnect_seconds: float) -> None:
    from .relay import run_relay_worker
    await run_relay_worker(data_dir, relay_host, relay_port, local_host=local_host, local_port=local_port or None, reconnect_seconds=reconnect_seconds)


async def _run_ice_signal(host: str, port: int) -> None:
    from .ice import IceSignalBroker
    broker = IceSignalBroker()
    bound = await broker.start(host, port)
    print(f"RHEXORA ICE signaling broker listening on {host}:{bound}")
    print("The random room token is a bearer secret. Use HTTPS/WSS through a trusted reverse proxy on public networks.")
    try:
        await asyncio.Future()
    finally:
        await broker.close()


async def _run_ice_worker(data_dir: str, signal_url: str, room: str, reconnect_seconds: float) -> None:
    from .ice import ice_settings_from_environment, run_ice_worker
    settings = ice_settings_from_environment()
    turn_shared_secret = os.getenv("RHEXORA_ICE_TURN_SHARED_SECRET") or None
    turn_ttl_seconds = int(os.getenv("RHEXORA_ICE_TURN_TTL_SECONDS", "3600"))
    if turn_shared_secret:
        # Credentials from the initial environment parse are replaced per reconnect
        # inside run_ice_worker, so a long-running worker never reuses expired TURN auth.
        settings["turn_username"] = None
        settings["turn_credential"] = None
    await run_ice_worker(
        data_dir,
        signal_url,
        room,
        reconnect_seconds=reconnect_seconds,
        turn_shared_secret=turn_shared_secret,
        turn_ttl_seconds=turn_ttl_seconds,
        **settings,
    )


async def _run_node(data_dir: str) -> None:
    server = RhexoraServer(data_dir)
    port = await server.start()
    print(f"RHEXORA node {server.config['node_id']} listening on {server.config.get('listen_host')}:{port}")
    print(f"TLS SHA-256 fingerprint: {server.fingerprint}")
    print("Press Ctrl+C to stop. Discovery does not automatically trust peers.")
    try:
        await server.serve_forever()
    finally:
        await server.close()


async def _submit_sum(args: argparse.Namespace) -> dict[str, Any]:
    values = _parse_values(args)
    peers = peers_from_data_dir(args.data_dir)
    return await RhexoraClient().distributed_sum(values, peers, chunk_size=args.chunk_size)


async def _submit_primes(args: argparse.Namespace) -> dict[str, Any]:
    peers = peers_from_data_dir(args.data_dir)
    return await RhexoraClient().distributed_prime_count(
        args.start, args.stop, peers, chunk_size=args.chunk_size
    )


async def _submit_video(args: argparse.Namespace) -> dict[str, Any]:
    peer = _load_peers_by_name(args.data_dir, args.peer)
    client = RhexoraClient(timeout=args.timeout)
    return await client.execute(peer, "media.video.transcode", {
        "input_file": args.input_file,
        "encoder": args.encoder,
        "allow_cpu_fallback": not args.no_cpu_fallback,
    })


async def _submit_distributed_video(args: argparse.Namespace) -> dict[str, Any]:
    from .video_distribution import distributed_video_transcode
    peers = peers_from_data_dir(args.data_dir)
    return await distributed_video_transcode(
        args.input, args.output, peers,
        segment_seconds=args.segment_seconds,
        encoder=args.encoder,
        timeout=args.timeout,
        max_in_flight=args.max_in_flight,
    )


async def _submit_text(args: argparse.Namespace) -> dict[str, Any]:
    try:
        text = Path(args.file).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise RhexoraError(f"Cannot read text file: {exc}") from exc
    peer = _load_peers_by_name(args.data_dir, args.peer)
    result = await RhexoraClient().execute(peer, "text.analyze", {"text": text})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            config = initialize_node(
                args.data_dir,
                listen_host=args.host,
                listen_port=args.port,
                max_items=args.max_items,
                max_concurrent_tasks=args.max_concurrent_tasks,
                enable_video=args.enable_video,
            )
            root = Path(args.data_dir).expanduser().resolve()
            fp = certificate_fingerprint(root / config["tls_cert"])
            print(f"Initialized node: {config['node_id']}")
            print(f"Data directory: {root}")
            print(f"TLS fingerprint: {fp}")
            print("Keep node.json and node-key.pem private. Create an invite only for a peer you trust.")
            return 0

        if args.command == "relay-credentials":
            output = Path(args.output).expanduser().resolve()
            credentials: dict[str, str] = {}
            for data_dir in args.node_dir:
                _, config = load_node_config(data_dir)
                credentials[str(config["node_id"])] = str(config.get("relay_token", ""))
                if len(credentials[str(config["node_id"])]) < 24:
                    raise RhexoraError(f"Node {data_dir!r} has no independent relay_token; reinitialize it before using relay")
            if output.exists():
                raise RhexoraError(f"Refusing to overwrite existing credential file: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(credentials, handle, indent=2)
                handle.write("\n")
            print(f"Private relay credentials created for {len(credentials)} worker(s): {output}")
            print("Protect this file; it contains the worker bearer tokens.")
            return 0

        if args.command == "relay-serve":
            asyncio.run(_run_relay_server(args.credentials, args.host, args.port, args.worker_wait_timeout))
            return 0

        if args.command == "relay-worker":
            asyncio.run(_run_relay_worker(args.data_dir, args.relay_host, args.relay_port, args.local_host, args.local_port, args.reconnect_seconds))
            return 0

        if args.command == "dht-run":
            bootstrap_items = list(args.bootstrap)
            if args.bootstrap_file:
                raw_bootstrap = json.loads(Path(args.bootstrap_file).expanduser().read_text(encoding="utf-8"))
                if not isinstance(raw_bootstrap, list) or any(not isinstance(item, str) for item in raw_bootstrap):
                    raise RhexoraError("Bootstrap file must be a JSON array of HOST:PORT strings")
                bootstrap_items.extend(raw_bootstrap)
            if not bootstrap_items:
                bootstrap_items.extend(item.strip() for item in os.getenv("RHEXORA_DHT_BOOTSTRAP", "").split(",") if item.strip())
            asyncio.run(_run_dht(args.data_dir, args.bind_host, args.advertise_host, args.port, bootstrap_items, args.allow_private_endpoints))
            return 0

        if args.command == "ice-room":
            import secrets
            print(secrets.token_urlsafe(32))
            return 0

        if args.command == "ice-signal-serve":
            asyncio.run(_run_ice_signal(args.host, args.port))
            return 0

        if args.command == "ice-worker":
            asyncio.run(_run_ice_worker(args.data_dir, args.signal_url, args.room, args.reconnect_seconds))
            return 0

        if args.command == "dht-peers":
            from .dht import DHTStore
            records = DHTStore(Path(args.data_dir).expanduser() / "dht-peers.sqlite3").all()
            print(json.dumps(records, indent=2, ensure_ascii=False))
            return 0

        if args.command == "run":
            if args.verbose:
                logging.basicConfig(level=logging.DEBUG)
            asyncio.run(_run_node(args.data_dir))
            return 0

        if args.command == "fingerprint":
            root, config = load_node_config(args.data_dir)
            print(certificate_fingerprint(root / config["tls_cert"]))
            return 0

        if args.command == "invite":
            root, config = load_node_config(args.data_dir)
            output = Path(args.output).expanduser().resolve()
            if not (1 <= int(config["listen_port"]) <= 65535):
                raise RhexoraError("This node uses port 0 or an invalid port; choose a fixed port before creating an invite")
            if bool(args.relay_host) != bool(args.relay_port):
                raise RhexoraError("--relay-host and --relay-port must be supplied together")
            if bool(args.ice_signal_url) != bool(args.ice_room):
                raise RhexoraError("--ice-signal-url and --ice-room must be supplied together")
            if args.ice_room and (len(args.ice_room) < 32 or len(args.ice_room) > 128 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for ch in args.ice_room)):
                raise RhexoraError("--ice-room must be a random URL-safe token of 32..128 characters")
            invite_data = {
                "invite_version": 1,
                "name": args.name,
                "host": args.host,
                "port": int(config["listen_port"]),
                "tls_fingerprint": certificate_fingerprint(root / config["tls_cert"]),
                "token": config["auth_token"],
                "node_id": config["node_id"],
                "max_items": int(config.get("max_items", 100_000)),
            }
            if args.relay_host:
                invite_data["relay_host"] = args.relay_host
                invite_data["relay_port"] = args.relay_port
                invite_data["relay_token"] = str(config["relay_token"])
            if args.ice_signal_url:
                invite_data["ice_signal_url"] = args.ice_signal_url
                invite_data["ice_room"] = args.ice_room
            output.write_text(json.dumps(invite_data, indent=2), encoding="utf-8")
            try:
                output.chmod(0o600)
            except OSError:
                pass
            print(f"Private peer invite written to: {output}")
            print("WARNING: This file grants access to this node. Share it only through a trusted private channel.")
            return 0

        if args.command == "peer":
            if args.peer_command == "add":
                peer = load_invite(args.invite_file)
                peers = load_peers(args.data_dir)
                if any(item.get("name") == peer.name for item in peers):
                    raise RhexoraError(f"A peer named {peer.name!r} already exists")
                peers.append(peer.to_dict())
                save_peers(args.data_dir, peers)
                print(f"Trusted peer added: {peer.name} ({peer.host}:{peer.port})")
                return 0
            if args.peer_command == "list":
                peers = load_peers(args.data_dir)
                if not peers:
                    print("No trusted peers configured.")
                for item in peers:
                    print(f"{item.get('name')}  {item.get('host')}:{item.get('port')}  node={item.get('node_id', 'unknown')}")
                return 0
            if args.peer_command == "remove":
                peers = load_peers(args.data_dir)
                remaining = [item for item in peers if item.get("name") != args.name]
                if len(remaining) == len(peers):
                    raise RhexoraError(f"No peer named {args.name!r}")
                save_peers(args.data_dir, remaining)
                print(f"Removed peer: {args.name}")
                return 0

        if args.command == "nat-check":
            from .stun import discover_mapped_address
            print(json.dumps(discover_mapped_address(args.server, args.port, timeout=args.timeout), indent=2))
            print("This is a UDP mapping observation only; it does not prove inbound reachability or TCP NAT traversal.")
            return 0

        if args.command == "media-capabilities":
            from .video import video_capabilities
            print(json.dumps(video_capabilities(), indent=2))
            return 0

        if args.command == "discover":
            peers = discover_peers(args.timeout)
            if not peers:
                print("No RHEXORA nodes discovered on this LAN.")
            else:
                for peer in peers:
                    caps = ", ".join(peer.get("capabilities", []))
                    print(f"{peer['node_id']}  {peer['host']}:{peer['port']}  capabilities=[{caps}]")
                    print(f"  TLS fingerprint: {peer['tls_fingerprint']}")
                print("Discovery is not trust. Verify fingerprints and exchange a private invite before submitting tasks.")
            return 0

        if args.command == "submit":
            if args.task_command == "sum":
                operation = _submit_sum(args)
            elif args.task_command == "primes":
                operation = _submit_primes(args)
            elif args.task_command == "text":
                operation = _submit_text(args)
            elif args.task_command == "video":
                operation = _submit_video(args)
            else:
                operation = _submit_distributed_video(args)
            result = asyncio.run(operation)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

        parser.error("Unknown command")
    except (RhexoraError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nRHEXORA stopped.")
        return 130
    return 0
