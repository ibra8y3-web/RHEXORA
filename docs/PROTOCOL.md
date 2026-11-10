# RHEXORA wire protocol 1

**Status:** alpha protocol. Version `1` is used by the Python worker/client and by the Android requester/worker implementation. Compatibility across releases is not yet guaranteed.

## Transport and framing

- Task and media requests use TLS over TCP, with TLS 1.2 or newer on the Python worker.
- A TCP connection carries exactly one request frame followed by one response frame, then closes.
- A frame is a 4-byte unsigned big-endian payload length followed by a UTF-8 JSON object.
- The default maximum frame is 8 MiB. Worker configuration may set a bounded value from 1 KiB to 64 MiB.
- Canonical JSON for hashes uses sorted keys, compact separators, UTF-8, and rejects NaN/Infinity.
- Every request contains `protocol_version: 1`, a unique `request_id`, and the destination worker's private bearer `token`.
- Clients must validate the response version and request ID. They must also pin the worker certificate SHA-256 fingerprint from a private invite.

## Execute request

```json
{
  "kind": "execute",
  "protocol_version": 1,
  "request_id": "uuid",
  "token": "private-node-token",
  "task": {
    "type": "math.sum_ints",
    "input": {"values": [1, 2, 3]}
  }
}
```

A successful response has `kind: result`, `ok: true`, `node_id`, the canonical task SHA-256 in `task_hash`, `result`, and the canonical result SHA-256 in `result_hash`. Errors set `ok: false` and provide an `error` string; an in-progress duplicate can set `retryable: true`.

The task hash binds the task body to the response. The result hash binds the returned result bytes. Neither proves honest or mathematically correct execution by a worker.

## Built-in task compatibility

The Python worker supports `math.sum_ints`, `math.count_primes`, and `text.analyze`; optional `media.video.transcode` is available only when the Python operator enables video. The Android requester and worker support the first three tasks only. They do not accept video, GPU, ICE, or arbitrary-code tasks.

- `math.sum_ints`: Python accepts bounded integers up to 4096 bits per value and configured `max_items`. Android accepts up to 100,000 signed 64-bit values and rejects a sum that overflows signed 64-bit. This is an Android subset of the Python input range.
- `math.count_primes`: Android accepts `0 <= start < stop`, a span of at most 200,000, and `stop <= 10^12`.
- `text.analyze`: returns Unicode code-point count, Python-compatible Unicode `\\w+` word count, and `str.splitlines()`-compatible line count. The Android requester UI caps its own input at 100,000 code points; the Android worker applies a larger bounded protocol limit subject to the 8 MiB frame cap.

Task support is selected by the worker's local allow-list, not by the client. An unsupported task returns `ok: false` and must not be retried as an untrusted code path.
The invite's `max_items` field is a workload limit advertised by the worker; clients should use it to avoid sending oversized work, but the worker must independently validate every request against its current local limits.

## Authenticated media upload

Media is disabled unless the worker operator explicitly enables it. Each operation is its own authenticated TLS request/response exchange. The client uses the same worker token and pinned certificate as task execution.

All upload requests use `kind: media.upload`, `protocol_version`, `request_id`, `token`, `operation`, and a random 32-character lowercase hexadecimal `upload_id`.

1. `start`: includes positive `size` (maximum 512 MiB) and lowercase SHA-256. The worker reserves one of at most eight active uploads and returns `chunk_bytes` (2 MiB).
2. `chunk`: includes the next zero-based `sequence` and base64 `data`. Decoded chunks are non-empty and at most 2 MiB. Sequence must be exactly the next expected value; bytes cannot exceed the declared size.
3. `finish`: accepted only after exactly the declared number of bytes arrive. The worker hashes the temporary file, compares the declared SHA-256, and atomically moves it to its generated `shared-media/uploads/<upload_id>.mp4` path.
4. `abort`: deletes the in-progress temporary upload.

The response uses `kind: media_transfer`, echoes the request ID and upload ID, and reports the acknowledged sequence/byte count or final file metadata. Upload state is held in memory; unfinished partial files are removed when the worker starts. Upload continuation across worker restart is not supported.

## Processed media download

A download request uses `kind: media.download`, the standard authentication fields, a generated `output_file` matching `<32-hex>.mp4`, a non-negative `offset`, and a `length` from 1 byte to 2 MiB. The worker returns the same output name, total `size`, `offset`, base64 `data`, and `eof`. The client downloads to a temporary file, verifies the complete expected SHA-256, and only then publishes the destination file.

Workers do not expose arbitrary filesystem paths or arbitrary shell commands through this protocol. Download names are restricted to generated processed-media outputs.

## LAN discovery

LAN discovery is an unauthenticated hint protocol, not a trust mechanism. IPv4 multicast group `239.255.77.77`, UDP port `47822`, is used. A `discover` datagram receives a `peer` response containing node ID, TCP port, TLS fingerprint and advertised task capabilities. No bearer token is broadcast. A discovered node must still be trusted through a private invitation and certificate pin.

## Relay transport

The optional self-hosted relay uses its own small control protocol and independent per-worker relay credentials. It pairs an outbound worker connector with a requester connection and forwards an opaque byte stream. The application TLS session is established end-to-end between requester and worker; the relay does not terminate task TLS. The relay can observe connection metadata and can deny or interrupt service. The relay control exchange is not encrypted; its authentication proof is a nonce-bound HMAC and relay tokens must remain private.

## DHT discovery

DHT messages are a separate signed UDP discovery protocol. Ed25519 public keys identify nodes; signed endpoint records bind a key to a derived 160-bit XOR node ID and expire. DHT records are untrusted discovery hints, never task invitations or proof of reachability. See [DHT design](DHT.md).
