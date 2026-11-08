# RHEXORA — Permissioned Peer-to-Peer Capability Network

> **Version 0.4.0a1 (alpha).** RHEXORA is an open-source peer-compute implementation with authenticated TLS workers, a signed UDP DHT prototype, WebRTC ICE DataChannel transport, an opaque self-hosted Relay fallback, distributed FFmpeg video processing and native Android requester/worker source. It is not a production 1.0 release.

## What is implemented

- Trusted peer invitations, TLS certificate pinning, per-node task tokens and an allow-listed task protocol.
- Python workers for integer sums, segmented prime counting, text statistics and FFmpeg video transcoding.
- Distributed video processing: split locally, authenticated chunk upload to multiple workers, remote H.264 encode, SHA-256 verified downloads and final assembly.
- Signed Ed25519 DHT node records, Kademlia-style XOR routing, bootstrap, lookup, record expiry and SQLite storage.
- WebRTC ICE DataChannel tunnel for Python peers. The existing worker TLS byte stream is preserved inside the DataChannel, including certificate pinning. STUN/TURN values are configurable through environment variables.
- Self-hosted opaque TCP Relay fallback with independent HMAC challenge authentication and end-to-end TLS.
- FFmpeg hardware encoder discovery for NVENC, VAAPI, QSV, AMF and VideoToolbox. A hardware encoder is advertised only after a real one-frame encode succeeds.
- Native Android source under `android/`, including requester and opt-in foreground Worker source.
- GitHub Actions for multi-platform Python tests, Android Gradle build, ICE integration, public network checks, GPU acceptance on a self-hosted GPU runner, CodeQL, Bandit, pip-audit, Gitleaks, Trivy and OpenSSF Scorecard.

## GitHub CI and external acceptance

See `GITHUB_CI_GUIDE_AR.md` for the complete upload/run guide, automatic workflows, operator-owned public-network prerequisites, GPU runner setup and independent audit requirements.

## Install and test

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

Install the optional ICE dependencies:

```bash
python -m pip install -e '.[ice,test]'
python -m pytest -q
```

## Start a worker

```bash
rhexora init --data-dir ./worker --port 47821
rhexora run --data-dir ./worker
```

Create a private invite, add it on the requester, then submit a computation. See `README_AR.md` for the full Arabic workflow.

## Public network deployment

- Global DHT bootstrap deployment: `docs/GLOBAL_DHT.md` and `ops/dht/compose.yaml`.
- WebRTC ICE, signaling and TURN deployment: `docs/ICE.md`, `ops/ice/compose.yaml`, and `Dockerfile.ice`.
- Relay deployment: `docs/NAT.md` and `ops_README_RELAY.md`.
- External GitHub smoke test configuration: `docs/PUBLIC_NETWORK_ACCEPTANCE.md`.

This repository does not publish invented DHT seed addresses or operate public infrastructure on behalf of users. Operators must deploy and configure their own public nodes, signaling service, TURN and Relay endpoints.

## GPU validation

```bash
rhexora media-capabilities
python scripts/gpu_acceptance.py
```

The acceptance script fails when no hardware encoder completes an actual encode with CPU fallback disabled. The GitHub GPU workflow requires a self-hosted runner labeled `rhexora-gpu` with a correctly installed GPU driver and FFmpeg build.

## Security status

The project includes automated security and supply-chain scans, but **has not received an independent security audit**. Use `SECURITY_AUDIT_REQUEST.md` as the scope for an external assessor. Do not expose workers to unknown peers or use the early alpha for sensitive workloads.

## Known external validation requirements

- ICE end-to-end integration tests are configured in GitHub Actions; the optional `aiortc` dependency could not be downloaded in the current offline build environment, so that integration test was skipped locally.
- Internet-wide DHT, public Relay and ICE tests require public nodes and secrets configured by the repository owner; the repository contains workflows and smoke scripts but does not claim these remote checks already passed.
- GPU support is implemented with real encoder probes, but this environment has no usable GPU device/driver; CPU encoding is the only path verified here.
- Android source is included and a GitHub build workflow is present. Physical-device validation remains a separate requirement.
- Independent security assessment must be performed by a third party.

## License

Apache License 2.0. See `LICENSE`.

## GitHub Actions failure bundles

If any workflow job fails, a dependent `failure-report` job automatically downloads the failed job logs and creates a ZIP artifact named `rhexora-build-failure-<run-id>-<attempt>`. Download it from the failed workflow run's **Artifacts** section. The bundle includes run/job metadata, downloadable logs, and complete copies of repository files whose paths appear in those logs or changed in the triggering commit.

A path mentioned in a log is evidence that the file appeared in the diagnostic output, not proof that it caused the failure. Common secret files/directories are excluded, but inspect the ZIP before sharing it; no filename-based filter can guarantee that arbitrary source files contain no secrets. The reporting job requires the GitHub Actions `actions: read` permission.
