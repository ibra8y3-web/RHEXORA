# Security policy

RHEXORA is alpha software and has **not** undergone an independent security audit. Do not expose workers to unknown peers or use the project for sensitive workloads.

Report suspected vulnerabilities privately to the project maintainers. Include the affected version, reproduction steps, expected behavior and observed impact. Do not publish invite files, relay tokens, ICE room capabilities, TURN credentials, node configuration files or private keys in bug reports.

Current safeguards include TLS 1.2+, pinned worker certificate fingerprints, per-node bearer tokens, a separate Relay HMAC credential, random ICE room capabilities, DTLS-protected WebRTC DataChannels, task allow-lists, JSON frame limits, input bounds, bounded concurrency, media path restrictions, and output SHA-256 verification. These safeguards do not make the system production-secure.

## Automated checks

GitHub Actions is configured for Python tests, Android build/unit tests, WebRTC ICE integration, optional public network smoke tests, GPU acceptance on a self-hosted GPU runner, CodeQL, Bandit, pip-audit, Gitleaks, Trivy, OpenSSF Scorecard and Dependabot. Automated results are useful signals; passing workflows are not a security certification and do not replace an independent review or penetration test.

The DHT remains a signed-discovery prototype. It validates signed endpoint records against the observed source IP and applies a bounded per-source-IP UDP token bucket, but it still needs adversarial protocol review, Sybil/eclipse resistance, application-level DoS controls and scale/churn validation. ICE room values are bearer secrets; use HTTPS/WSS and keep invites private. TURN credentials can use short-lived coturn REST authentication; keep the shared secret private. A TURN/Relay operator can observe metadata and deny service, but the original worker TLS task payload is carried end-to-end. Remote execution is restricted to built-in allow-listed operations; arbitrary remote code execution is intentionally not supported.

The independent audit scope and requested deliverables are documented in `SECURITY_AUDIT_REQUEST.md`. Do not mark the audit complete until an independent assessor submits a dated report and retest findings.
