# RHEXORA security design (alpha)

This document describes protections present in the source and their limits. It is not a security certification or independent audit.

## Trust establishment

Task and media requests use private invitations containing the destination endpoint, node ID, TLS certificate SHA-256 fingerprint and random worker bearer token. Invitations must be transferred through a private authenticated channel. LAN multicast and DHT announcements are discovery hints only and do not establish trust.

The Python DHT uses a separate Ed25519 identity key. Endpoint records are signed, expire and bind the public key to a derived 160-bit node ID. Lookup responses are checked against the expected target identity, sequence numbers cannot rewrite an endpoint at the same sequence, and local storage is bounded. This protects record integrity, but does not prove reachability, honesty, uniqueness or safety. Sybil/eclipsing resistance, global replication/quorum and adversarial large-scale hardening are not implemented.

The optional relay uses a separate per-node `relay_token`, not the worker task token. A fresh nonce-bound HMAC proof authorizes worker registration/requester pairing. The relay forwards the requester-to-worker TLS stream without terminating it. The relay can still observe connection metadata, refuse service or disrupt sessions. Its control exchange is not encrypted; the task data stream remains end-to-end TLS encrypted.

## Protections implemented

- Python worker TLS enforces TLS 1.2 or newer. Python and Android clients pin the invited worker's SHA-256 certificate fingerprint.
- Worker token and Relay HMAC comparisons use constant-time comparison.
- Workers execute only locally installed, explicitly enabled task types. Arbitrary remote code execution is not exposed.
- Request frame size, input counts, text size, media file size, media chunk size, active upload count, task concurrency and task-specific work ranges are bounded.
- Media uploads use ordered chunks, temporary files, SHA-256 verification and generated storage names. Media downloads are limited to generated output filenames and client-side full-file SHA-256 verification.
- FFmpeg is invoked without a shell and uses a fixed encoder allow-list. Media is parsed by FFmpeg only on workers whose operator enabled the media capability.
- Worker SQLite state stores request IDs, canonical task hashes and responses for a bounded retention period. This is idempotency assistance, not exactly-once execution across every crash point.
- Android worker is an explicitly started foreground service with an ongoing notification. It has a persistent app-private TLS identity, bounded prime-count task support, a 14-day SQLite completion ledger, bounded concurrency, and an optional outbound Relay connector. It does not start automatically at boot and does not hold a permanent CPU wake lock.
- Python ICE signaling uses an unguessable 256-bit room bearer capability. WebRTC DataChannel DTLS carries the original RHEXORA TLS stream; requester-side certificate pinning remains active. Public signaling is required to use HTTPS/WSS by peer invite validation.
- The Docker Compose Relay example runs as an unprivileged UID, with a read-only root filesystem, read-only secret mount, dropped Linux capabilities and `no-new-privileges`.
- CI workflows include CodeQL, Bandit, pip-audit, Gitleaks, Trivy and OpenSSF Scorecard. These checks are automated project safeguards, not a substitute for external review.

## Important risks and unresolved areas

- A trusted worker can lie about a result. A result hash validates payload consistency, not mathematical correctness. General independent result verification and replicated quorum are not implemented.
- An invite token is a bearer secret. Anyone holding it can submit every enabled task until the operator revokes or rotates access.
- A malicious media file is parsed by FFmpeg. Codec vulnerabilities remain possible; keep FFmpeg patched and accept media only from trusted invite holders.
- DHT is experimental and vulnerable to Sybil, eclipse, routing manipulation, denial of service, churn and metadata exposure. The 16-node test is a local overlay, not a global Internet test.
- STUN reports an observed UDP mapping only. Python ICE negotiation and a DataChannel transport are implemented, but direct connectivity is not guaranteed across all NAT types; TURN is needed for symmetric/CGNAT/restrictive networks. Public Internet and cross-ISP/CGNAT ICE/Relay tests have not been completed. The self-hosted relay is an optional fallback and an availability dependency; expose it only behind a reviewed firewall/rate-limit policy.
- Android source has not yet passed a full Android Gradle APK build in the local environment or a physical-device test. Android worker currently handles bounded prime-count tasks only; it does not accept media uploads or run FFmpeg/GPU tasks. Android battery saver, thermal and OEM background restrictions may suspend work.
- CPU, memory, disk, thermal and battery governance are not comprehensive. There is no general sandbox for arbitrary code because arbitrary code is not accepted.
- Cached results and media files remain on local disk until operator cleanup/expiry; protect the node data directory and media storage.
- No independent security audit, external penetration test, independent fuzzing campaign, formal verification or third-party remediation retest has been performed.

Do not process sensitive personal data or valuable production workloads on untrusted nodes. Do not expose worker ports publicly without an explicit firewall and access-control policy. Never commit invitation tokens, Relay tokens, TLS private keys or Relay credential files to source control.
