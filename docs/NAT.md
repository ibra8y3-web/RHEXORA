# Internet connectivity, ICE, STUN, TURN and Relay

RHEXORA supports three transport choices for a trusted Python peer:

1. Direct TCP/TLS when a peer has a reachable address/port.
2. WebRTC ICE DataChannel carrying the original pinned TCP/TLS stream.
3. The optional self-hosted opaque TCP Relay as a fallback.

The requester attempts ICE first when an invite contains ICE settings, then uses the configured Relay if ICE negotiation/connection fails. Without ICE details, it uses Relay when configured, otherwise direct TCP.

## STUN mapping diagnostics

```bash
rhexora nat-check --server stun.l.google.com --port 19302 --timeout 3
```

A STUN response reports the UDP-mapped address observed by one server. It is not proof of inbound reachability, direct peer-to-peer connectivity, or a TCP mapping.

## ICE signaling and TURN deployment

See `docs/ICE.md` and `ops/ice/compose.yaml`. A public deployment uses:

- A WSS signaling broker (Caddy handles HTTPS/WSS in the included Compose stack).
- STUN servers for candidate discovery.
- A TURN server for restrictive/symmetric NAT and carrier-grade NAT cases.
- A random 256-bit room token shared between worker and requester.

The ICE DataChannel uses DTLS. The original RHEXORA TLS stream is tunneled inside it, so the requester still pins the worker's existing TLS certificate. Configure `RHEXORA_ICE_TURN_URL` and the same `RHEXORA_ICE_TURN_SHARED_SECRET` on both peers. Each process generates a coturn REST-auth username and HMAC-SHA1 credential with a bounded one-hour lifetime by default. Static username/credential environment variables remain available for compatibility but are not the recommended public deployment mode.

## Self-hosted opaque Relay

If ICE cannot establish a direct path, a self-hosted TCP Relay can provide the fallback. The worker maintains an outbound connection; the requester authenticates with a separate per-worker Relay credential. The Relay forwards the bytes without terminating the inner worker TLS session.

```bash
rhexora relay-credentials --output ./relay-credentials.json --node-dir ./worker-a
rhexora relay-serve --credentials ./relay-credentials.json --host 0.0.0.0 --port 47825
rhexora relay-worker --data-dir ./worker-a --relay-host relay.example.net --relay-port 47825
```

Add Relay details to a private invite with `--relay-host` and `--relay-port`. See `ops_README_RELAY.md` for the hardened Docker deployment.

## Public acceptance tests

Set GitHub Actions variables/secrets as documented in `docs/PUBLIC_NETWORK_ACCEPTANCE.md` and run the **Public network smoke tests** workflow. In strict mode it requires STUN mapping, each configured public DHT seed to return signed neighbors and at least one reachable neighbor, Relay TCP reachability, a real Relay task, and a real ICE DataChannel + pinned-TLS task using a live worker invite.

Local tests do not prove cross-ISP reliability. Symmetric NAT, carrier-grade NAT, provider firewall policy, UDP blocking, IPv6-only networks and TURN bandwidth/availability must be checked using the actual target networks. No public Relay, TURN server or global ICE rendezvous service is operated by this repository maintainer.
