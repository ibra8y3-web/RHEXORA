import asyncio

import pytest

from rhexora.errors import ProtocolError
from rhexora.protocol import canonical_json, read_message, write_message


@pytest.mark.asyncio
async def test_protocol_round_trip():
    reader = asyncio.StreamReader()
    class Writer:
        def __init__(self):
            self.buffer = bytearray()
        def write(self, data):
            self.buffer.extend(data)
        async def drain(self):
            return None

    writer = Writer()
    payload = {"kind": "test", "value": [1, 2, 3]}
    await write_message(writer, payload)
    reader.feed_data(bytes(writer.buffer))
    decoded = await read_message(reader)
    assert decoded == payload
    assert canonical_json(decoded) == canonical_json(payload)


@pytest.mark.asyncio
async def test_protocol_rejects_oversized_frame():
    reader = asyncio.StreamReader()
    reader.feed_data((100).to_bytes(4, "big"))
    with pytest.raises(ProtocolError, match="Invalid frame length"):
        await read_message(reader, max_frame_bytes=20)
