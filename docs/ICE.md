# RHEXORA ICE transport

RHEXORA now has an optional WebRTC ICE transport for its existing TCP/TLS worker protocol. The WebRTC DataChannel is used as a byte tunnel; the original TLS session is carried unchanged inside it. The requester still verifies the worker's existing SHA-256 certificate pin before sending the task token or payload. The signaling broker only exchanges SDP and does not receive task data.

## Install

```bash
python -m pip install -e '.[ice]'
```

## Deploy signaling + TURN on a public VPS

1. Point `signal.example.com` and `turn.example.com` to a public VPS.
2. Open TCP 80/443, TCP+UDP 3478, and UDP 49160-49200 in both the VPS firewall and provider firewall.
3. From the repository root, create the private deployment files and lock their permissions:

```bash
cp .env.ice.example ops/ice/.env
cp ops/ice/turnserver.conf.example ops/ice/turnserver.conf
chmod 600 ops/ice/.env ops/ice/turnserver.conf
```

Replace the signaling domain, TURN realm, public IP and `static-auth-secret` placeholder. Use a long random shared secret of at least 32 characters. The real coturn configuration is git-ignored and mounted read-only; never commit it.
4. Start the signaling broker, Caddy reverse proxy (automatic HTTPS/WSS) and coturn:

```bash
cd ops/ice
docker compose up --build -d
```

The signaling broker limits active rooms and signaling messages per connection. The TURN deployment uses coturn REST authentication with a shared secret stored in `ops/ice/turnserver.conf`. Each requester session generates a short-lived username/password pair (default lifetime one hour); the long-running ICE worker creates a fresh pair after every reconnect so it does not reuse expired TURN credentials. Set `RHEXORA_ICE_TURN_SHARED_SECRET` to exactly the same secret on requesters and workers. Never commit the private config or disclose the shared secret.

## Pair a worker and requester

On the worker, create a random room capability:

```bash
ROOM="$(rhexora ice-room)"
echo "$ROOM" # copy it through a private channel only
```

Start the TLS worker in one terminal:

```bash
rhexora run --data-dir ./worker
```

Start the ICE worker in another terminal. Provision the same shared secret securely on this worker host (do not paste it into a shared shell history):

```bash
export RHEXORA_ICE_TURN_URL='turn:turn.example.com:3478?transport=udp'
export RHEXORA_ICE_TURN_SHARED_SECRET='YOUR_SECURELY_PROVISIONED_SHARED_SECRET'
rhexora ice-worker --data-dir ./worker \
  --signal-url 'https://signal.example.com' --room "$ROOM"
```

Create an invite containing the same public signaling URL and random room token:

```bash
rhexora invite --data-dir ./worker --host worker.invalid \
  --name worker-ice --output worker-ice.invite.json \
  --ice-signal-url 'https://signal.example.com' --ice-room "$ROOM"
```

Send the invite file securely to the requester. The `host` value remains the direct endpoint/fallback; the ICE transport uses the signaling URL and room token. Add the invite on the requester:

```bash
rhexora peer add ./worker-ice.invite.json --data-dir ./requester
rhexora submit primes --data-dir ./requester --start 1 --stop 100000
```

RHEXORA tries ICE first, then the opaque Relay if configured, and finally direct TCP. Certificate-pin failures are security errors and never trigger a transport downgrade. Configure `RHEXORA_ICE_STUN` as a comma-separated list if using custom STUN servers. Configure TURN values as environment variables; provision the shared secret through a secret manager or protected environment file rather than command history.

## Security and NAT limits

- Room values are 256-bit random bearer capabilities. Keep them private; anyone who obtains one can race to join that short-lived signaling room.
- Use HTTPS/WSS. Do not expose the plain HTTP signaling broker directly to the Internet.
- The ICE DataChannel uses WebRTC DTLS. Inside it, the original RHEXORA TLS session remains end-to-end and the requester checks the worker certificate pin.
- STUN is only address discovery; it does not guarantee direct connectivity. Some symmetric NATs, carrier-grade NATs and restrictive enterprise networks require TURN. TURN relays encrypted packets and can observe traffic volume and endpoints, but does not decrypt the inner RHEXORA TLS stream.
- A loopback ICE integration test verifies the real DataChannel-to-TLS bridge. A public ICE test requires a public WSS signaling URL, a live remote worker and valid STUN/TURN configuration; see `scripts/public_network_smoke.py` and `.github/workflows/public-network.yml`.
