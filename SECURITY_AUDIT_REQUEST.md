# Independent security audit request — RHEXORA

**Status: NOT PERFORMED.** This file is a scope and evidence checklist for an independent security assessor. Automated repository scans and maintainer tests are not an independent audit and must not be described as one.

## Requested assessor scope

1. Threat model and architecture review for peer identity, invite secrets, task authorization, relay credentials, ICE room capabilities, and the DHT.
2. Python protocol/parser review: frame limits, canonical JSON, request replay/idempotency, concurrency, file transfer, path traversal, resource exhaustion and denial of service.
3. TLS pinning and certificate lifecycle review across direct, Relay and ICE paths.
4. Relay and ICE signaling review: authentication, room races, abuse limits, metadata leakage, TURN configuration, and transport downgrade behavior.
5. DHT review: signed peer records, replay/staleness, Sybil/eclipsing attacks, amplification, malformed UDP packets and routing-table behavior under churn.
6. Android review: foreground service lifecycle, background execution limits, permissions, secret storage, TLS pinning and resource limits.
7. FFmpeg/GPU worker review: sandboxing, subprocess arguments, hostile media parsing, filesystem boundaries, memory/CPU/GPU exhaustion and cancellation.
8. Dependency, build, release and CI workflow review, including GitHub Actions permissions and supply-chain controls.

## Deliverables required from the independent assessor

- Dated report identifying commit SHA and exact versions reviewed.
- Severity-ranked findings with reproducible proof-of-concept steps.
- Explicit coverage and exclusions.
- Retest report for all critical/high findings after fixes.

Do not mark this audit complete until a qualified party independent of the project authors has delivered the report and the report is published or its publication status is recorded. Report vulnerabilities privately using `SECURITY.md`.
