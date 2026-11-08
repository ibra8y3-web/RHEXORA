"""Local node identity and self-signed TLS certificate management."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .errors import ConfigurationError

CONFIG_FILE = "node.json"
CERT_FILE = "node-cert.pem"
KEY_FILE = "node-key.pem"
PEERS_FILE = "peers.json"


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def certificate_fingerprint(cert_path: Path) -> str:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Cannot read TLS certificate: {exc}") from exc
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def initialize_node(
    data_dir: str | Path,
    *,
    listen_host: str = "0.0.0.0",
    listen_port: int = 47821,
    max_items: int = 100_000,
    max_frame_bytes: int = 8 * 1024 * 1024,
    max_concurrent_tasks: int = 2,
    enable_video: bool = False,
) -> dict[str, Any]:
    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / CONFIG_FILE
    if config_path.exists():
        raise ConfigurationError(f"Node already initialized in {root}")
    if not (0 <= listen_port <= 65535):
        raise ConfigurationError("listen_port must be between 0 and 65535")
    if max_items < 1 or max_items > 1_000_000:
        raise ConfigurationError("max_items must be between 1 and 1000000")
    if not (1024 <= max_frame_bytes <= 64 * 1024 * 1024):
        raise ConfigurationError("max_frame_bytes must be between 1 KiB and 64 MiB")
    if not (1 <= max_concurrent_tasks <= 64):
        raise ConfigurationError("max_concurrent_tasks must be between 1 and 64")

    node_id = str(uuid.uuid4())
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, f"RHEXORA node {node_id}")]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("rhexora.local")]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    _write_private(
        root / KEY_FILE,
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ),
    )
    (root / CERT_FILE).write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    config: dict[str, Any] = {
        "schema_version": 1,
        "node_id": node_id,
        "listen_host": listen_host,
        "listen_port": listen_port,
        "auth_token": secrets.token_urlsafe(32),
        "relay_token": secrets.token_urlsafe(32),
        "tls_cert": CERT_FILE,
        "tls_key": KEY_FILE,
        "allowed_tasks": ["math.sum_ints", "math.count_primes", "text.analyze"] + (["media.video.transcode"] if enable_video else []),
        "media_enabled": bool(enable_video),
        "max_items": max_items,
        "max_frame_bytes": max_frame_bytes,
        "max_concurrent_tasks": max_concurrent_tasks,
        "discovery_enabled": True,
    }
    if enable_video:
        (root / "shared-media").mkdir(exist_ok=True)
        (root / "processed-media").mkdir(exist_ok=True)
    _write_private(config_path, json.dumps(config, indent=2).encode("utf-8"))
    _write_private(root / PEERS_FILE, b"[]\n")
    try:
        (root / PEERS_FILE).chmod(0o600)
    except OSError:
        pass
    return config


def load_json_file(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Cannot read {path}: {exc}") from exc


def load_node_config(data_dir: str | Path) -> tuple[Path, dict[str, Any]]:
    root = Path(data_dir).expanduser().resolve()
    config = load_json_file(root / CONFIG_FILE)
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ConfigurationError(f"No valid RHEXORA node config found in {root}")
    required = ("node_id", "auth_token", "tls_cert", "tls_key", "allowed_tasks")
    if any(not config.get(key) for key in required):
        raise ConfigurationError("Node configuration is missing required fields")
    for key in ("tls_cert", "tls_key"):
        if not (root / config[key]).is_file():
            raise ConfigurationError(f"Missing TLS file: {root / config[key]}")
    return root, config


def load_peers(data_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(data_dir).expanduser().resolve()
    peers = load_json_file(root / PEERS_FILE, default=[])
    if not isinstance(peers, list):
        raise ConfigurationError("peers.json must contain a JSON list")
    return peers


def save_peers(data_dir: str | Path, peers: list[dict[str, Any]]) -> None:
    root = Path(data_dir).expanduser().resolve()
    path = root / PEERS_FILE
    _write_private(path, json.dumps(peers, indent=2).encode("utf-8"))
