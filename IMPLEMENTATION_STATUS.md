# RHEXORA implementation and verification status

**Version:** `0.4.0a1` (alpha; not a production 1.0 release)  
**License:** Apache-2.0

This report deliberately distinguishes implementation in source from tests that require external infrastructure or physical hardware.

## Implemented in this repository

### Core peer-compute protocol
- Python node identity, TLS certificates, SHA-256 certificate pinning, per-node bearer tokens and private peer invites.
- Direct TLS/TCP worker connections and allow-listed task handlers for integer sums, prime counting and text statistics.
- Bounded task execution and JSON frames, SQLite idempotency ledger, input limits and concurrency controls.

### Signed DHT
- Ed25519 identities and expiring, sequence-numbered signed node records.
- XOR-distance routing buckets, UDP ping/announce/find-node, bootstrap, iterative lookup and SQLite record persistence.
- `--bootstrap`, `--bootstrap-file` and `RHEXORA_DHT_BOOTSTRAP` configuration.
- Non-mutating signed `health` probes for external bootstrap checks.
- Incoming UDP records must be signed by their advertised identity and the advertised host must resolve to the observed source IP; inbound datagrams use a bounded per-source-IP token bucket.
- A multi-node local overlay test using independent node keys and stores.
- Public DHT seed deployment Compose file and documentation under `ops/dht/` and `docs/GLOBAL_DHT.md`.

### Public transport / NAT
- STUN Binding Request diagnostics.
- Optional WebRTC ICE transport using aiortc and an ephemeral WebSocket SDP signaling broker.
- ICE DataChannel transports the original TCP/TLS byte stream, preserving end-to-end TLS and worker certificate pinning.
- Optional TURN server configuration via environment variables for restrictive/symmetric NAT.
- Client tries configured ICE first, then an independently configured opaque Relay, then direct TCP.
- A self-hosted TCP Relay with per-worker HMAC challenge authentication and outbound worker connectors.
- Public-network smoke script and GitHub workflow capable of testing configured public DHT seeds, STUN, Relay reachability, Relay task execution and an actual ICE task against a live worker.

**Important:** ICE source and CI integration test are present, but aiortc could not be downloaded in this offline environment, so the actual WebRTC loopback integration test did not run here. Public cross-ISP ICE/Relay/NAT behavior has not been tested from this environment. The public workflow needs operator-owned infrastructure and secrets.

### GPU video processing
- FFmpeg capability discovery for NVIDIA NVENC, VAAPI, Intel QSV, AMD AMF and Apple VideoToolbox.
- Hardware encoders are only marked usable after a real one-frame encode probe; a listed encoder without a working driver/device is not reported as available.
- Specific hardware encoder requests fail instead of silently switching to CPU when fallback is disabled.
- CPU and distributed video pipelines remain supported; outputs are inspected with FFprobe and verified with SHA-256 transfer hashes.
- Strict GPU acceptance script and a self-hosted GitHub workflow requiring runner label `rhexora-gpu`.

**Current machine result:** FFmpeg exposes several hardware encoder names, but no hardware encoder passed a real encode probe. This environment has no usable GPU device/driver, so only CPU H.264 encoding has been exercised. GPU support must be validated on an actual supported host.

### Native Android
- Kotlin/Jetpack Compose requester and opt-in foreground Worker source under `android/`.
- Direct TLS and opaque Relay path, TLS pinning, task/result hash validation and bounded `math.count_primes`, `math.sum_ints`, and `text.analyze` tasks.
- The Android UI has Home, Tasks and Settings destinations, invite JSON file/paste import, validated task inputs, worker status, light/dark/system appearance and persisted Relay settings.
- Android Gradle build/unit-test workflow is included.

Android does not currently use the new Python WebRTC ICE transport or DHT discovery. The Android worker accepts only the three built-in bounded tasks above; Android video/FFmpeg/GPU work is not implemented. Physical-device validation remains necessary for service lifecycle, UI/RTL, battery/OEM policies, direct networking and Relay operation.

### Security and supply-chain automation
- Existing CodeQL, Bandit, pip-audit and Dependabot automation.
- Gitleaks, Trivy filesystem/config scanning and OpenSSF Scorecard workflow added.
- Formal independent audit request and scope in `SECURITY_AUDIT_REQUEST.md`.

**Independent audit:** NOT PERFORMED. Automated tools and project-authored tests are not an independent audit. A qualified third party must review the code and provide a dated report and retest.

## Verification performed in this environment

- `python -m compileall -q src scripts tests`: passed.
- `python -m pytest -q`: **56 passed, 1 skipped**.
- The skipped test is `tests/test_ice.py`, which requires the optional `aiortc` extra. Package download failed because DNS/network access to the Python package index is unavailable in this environment; the GitHub ICE workflow installs the extra and runs the real DataChannel/TLS integration test.
- Python CLI `--version` and help checked; new commands `ice-signal-serve`, `ice-worker`, and `ice-room` appear.
- `python scripts/gpu_acceptance.py` was run; it correctly exited with code 2 because NVENC was not permitted, VAAPI device `/dev/dri/renderD128` was absent, and QSV device creation failed. No GPU pass is claimed.
- `python scripts/public_network_smoke.py --help` and CLI `--version`/help were checked. The strict public smoke script, GitHub ICE/GPU/public-network workflows, and DHT/ICE deployment Compose files are authored but have not been run against user-owned public infrastructure.

## Remaining external acceptance criteria

1. Run GitHub Actions after pushing. Resolve any build/test/scanner errors surfaced by the repository.
2. Configure at least three public DHT bootstrap nodes on independent hosts/providers; run `Public network smoke tests` with `RHEXORA_DHT_BOOTSTRAP` set and verify all signed health probes pass.
3. Deploy WSS signaling, TURN and Relay; keep a worker online; configure the invite and GitHub secrets; verify real ICE task and Relay task checks pass from GitHub-hosted runners.
4. Register a GPU-equipped self-hosted runner labeled `rhexora-gpu`; run the GPU acceptance workflow. A green GPU job is required before claiming GPU hardware was tested.
5. Build APKs with the Android workflow and test on physical Android devices, including process restart, battery policy, direct LAN and Relay operation.
6. Commission an independent security audit and resolve/retest its findings. See `SECURITY_AUDIT_REQUEST.md`.

## Trust boundaries and known limitations

- A result hash proves byte integrity, not that a worker performed an honest calculation. There is no general verifiable-computation or replicated consensus mechanism.
- DHT records are signed self-asserted endpoint metadata, not proof of reachability or task trust. Source-IP binding and per-IP token buckets provide basic abuse resistance, not Sybil/eclipse resistance; broader adversarial testing, application-level DoS controls, IPv6 validation and production hardening remain necessary.
- ICE room tokens are bearer capabilities. Use random 256-bit values, HTTPS/WSS, private invites and TURN credentials with least privilege. A public TURN server can relay encrypted traffic but cannot decrypt the inner task TLS stream.
- Relay, signaling, DHT and TURN operators can observe network metadata and deny service.
- Hardware encoder probes prove one test encode on that machine only; they do not guarantee stability, throughput, or all input compatibility.
- Android does not yet support the Python ICE tunnel or video/GPU worker operations.
- No independent security audit, public network certification or production-scale load test has been performed.

## Prior-art honesty

Distributed volunteer computing and pooling device resources are established fields. RHEXORA is an independent open-source implementation and must not be represented as the first such invention or globally novel without a separate prior-art and patent review.
