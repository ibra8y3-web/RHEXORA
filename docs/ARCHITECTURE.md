# RHEXORA architecture (0.4 alpha)

## Nodes and trust

Each compute node has a TLS server identity and a separate application bearer token. A requester receives a private invitation containing the endpoint, certificate fingerprint, node ID and token. The TLS certificate is pinned before task data is sent. Discovery records never create execution trust.

## Control and discovery planes

- Optional IPv4 multicast is limited to LAN discovery.
- The UDP DHT uses a distinct Ed25519 identity and signed, expiring endpoint records. Incoming records must bind to the observed source IP, and inbound datagrams are subject to per-source-IP token-bucket limits. Its Kademlia-style XOR table is used only to discover candidate endpoints.
- A discovered candidate still requires a manually trusted private invitation and a pinned TLS identity before task execution.
- The optional Relay uses separate worker credentials and one-use HMAC challenge-response. It forwards the requester-to-worker TLS byte stream without terminating it.

## Compute plane

The Python server accepts only built-in allow-listed task types, applies input/frame/concurrency limits, hashes canonical task and result JSON, and records completed requests in SQLite. The current task set is integer aggregation, prime counting and text statistics; optional video processing is a separate Python-only media plane. The Android requester and worker support those three compute tasks with Android-specific signed-64-bit sum limits and bounded input sizes.

A SHA-256 result digest verifies response-byte integrity but does not prove that the worker calculated the answer honestly. There is no general proof-of-computation or quorum verification protocol yet.

## Media plane

The requester can upload explicitly selected media to an authenticated worker using ordered, size-bounded TLS chunks. The worker stores uploads in a staging directory, verifies SHA-256 before making the file available, and processes only the pre-defined FFmpeg task. Generated output IDs are confined to the worker's processed-media directory. Downloads use bounded chunks and are hash-verified by the requester.

For distributed video, the requester creates independently decodable segments, distributes them across trusted workers, downloads the processed segments, and assembles them locally. The tested path uses CPU H.264 encoding. Actual GPU execution requires compatible host hardware, drivers and a successful real encode probe.

## Android runtime

The native Android app is both a requester and an optional user-started worker. The worker runs as a foreground service of type `connectedDevice`, with an ongoing notification and runtime notification permission request on Android 13+. It maintains an app-private TLS identity, listens on TCP port 47821, and can maintain an outbound authenticated Relay tunnel. The service has bounded concurrency, a strict task allow-list, and an SQLite completed-request ledger. Android battery saver, OEM restrictions and network handover still require physical-device validation. The `connectedDevice` type is used because the service maintains network interactions with remote peer devices; Android documents this type and its prerequisite permissions in the official foreground-service guide.

## Deployment

The Relay can be self-hosted directly with the CLI or using the Docker Compose example. It is intentionally not a mandatory coordinator; direct requester-to-worker TLS is preferred when reachable, with Relay as a user-configured fallback. The container uses an unprivileged UID and read-only filesystem settings.
