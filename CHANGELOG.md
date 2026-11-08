# Changelog

## 0.4.0a1 — 2026-11-08

- Added optional WebRTC ICE signaling and DataChannel transport for Python peers. The existing worker TLS stream is tunnelled inside the DataChannel; certificate pinning remains active.
- Added ICE-first transport selection with opaque Relay fallback and direct TCP fallback.
- Added configurable STUN/TURN servers through environment variables and an ICE worker connector.
- Added `ice-room`, `ice-signal-serve` and `ice-worker` CLI commands; peer invites can carry HTTPS/WSS signaling URL and a random room capability.
- Added non-mutating signed DHT health requests, bootstrap-file support and `RHEXORA_DHT_BOOTSTRAP` environment configuration.
- Added global DHT seed Docker Compose deployment and public-network acceptance workflow.
- Expanded FFmpeg real hardware encoder probing to NVENC, VAAPI, QSV, AMF and VideoToolbox; added strict GPU acceptance script and self-hosted GitHub workflow.
- Added external network smoke tests for STUN, DHT bootstrap, Relay and ICE/Relay task execution.
- Added Gitleaks, Trivy and OpenSSF Scorecard workflow; added `SECURITY_AUDIT_REQUEST.md` for an independent third-party review.
- Updated README, NAT/ICE/GPU/DHT docs and implementation status to mark remote/hardware/security tests honestly.
- Added bounded ICE signaling rooms/messages and short-lived coturn REST-auth credentials generated from a private shared secret.
- ICE workers rotate coturn REST credentials on reconnect instead of reusing credentials that may have expired during long runtimes.
- Hardened DHT ingress with source-IP binding, per-IP UDP token buckets, and read-only signed-neighbor health probes for external acceptance.
- Added strict public-network acceptance that proves signed DHT neighbors are reachable and executes actual task requests over ICE and Relay without allowing direct-TCP fallback to mask failure.
- Added a real hardware-encode acceptance workflow that fails when no GPU encoder works; CPU fallback cannot produce a false GPU pass.
- Updated deployment examples, private coturn configuration handling, and audit documentation. Real WAN, GPU, and independent audit results remain external acceptance requirements.

## 0.3.0a1 — 2026-11-08

- Added core peer-compute protocol, signed DHT prototype, self-hosted opaque TLS Relay, authenticated chunked media transfer and distributed FFmpeg processing.
- Added native Android requester and foreground Worker source.
- Added Python/Android build and security automation.
