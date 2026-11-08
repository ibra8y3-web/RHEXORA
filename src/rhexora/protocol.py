"""Small bounded JSON framing protocol used over TLS TCP streams."""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

from .errors import ProtocolError

DEFAULT_MAX_FRAME_BYTES = 8 * 1024 * 1024
_HEADER = struct.Struct("!I")


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"Message is not valid JSON data: {exc}") from exc


async def read_message(
    reader: asyncio.StreamReader,
    *,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> dict[str, Any]:
    try:
        raw_header = await reader.readexactly(_HEADER.size)
        (length,) = _HEADER.unpack(raw_header)
        if length < 2 or length > max_frame_bytes:
            raise ProtocolError(f"Invalid frame length: {length}")
        raw = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("Connection closed during a protocol frame") from exc

    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Frame is not valid UTF-8 JSON") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Protocol frame must contain a JSON object")
    return message


async def write_message(
    writer: asyncio.StreamWriter,
    message: dict[str, Any],
    *,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> None:
    raw = canonical_json(message)
    if len(raw) > max_frame_bytes:
        raise ProtocolError(
            f"Message is {len(raw)} bytes; limit is {max_frame_bytes} bytes"
        )
    writer.write(_HEADER.pack(len(raw)) + raw)
    await writer.drain()
