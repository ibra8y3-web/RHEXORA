# RHEXORA DHT discovery layer (alpha)

The `rhexora.dht` module implements an experimental Kademlia-style UDP overlay for **signed peer endpoint records**. It is independent from task-execution trust.

## Record security

Each DHT node stores an Ed25519 private key in `dht-ed25519-private.pem` (mode `0600` on POSIX). The DHT node ID is the first 160 bits of SHA-256 over the raw Ed25519 public key. Every advertised endpoint record carries the public key, a monotonically increasing sequence number, an expiry, and an Ed25519 signature over canonical JSON. Receivers verify the signature, key-derived node ID, endpoint shape, sequence and expiry before storing the record.

## Run

Start a first node on a reachable host:

```sh
rhexora dht-run --data-dir ./node-a --advertise-host 198.51.100.10 --bind-host 0.0.0.0 --port 47823
```

Start another node and bootstrap it from the first:

```sh
rhexora dht-run --data-dir ./node-b --advertise-host 198.51.100.11 --bind-host 0.0.0.0 --port 47823 --bootstrap 198.51.100.10:47823
```

Inspect stored records:

```sh
rhexora dht-peers --data-dir ./node-b
```

Bootstrap from multiple public seeds with a JSON list or environment variable:

```json
["dht1.example.net:47823", "dht2.example.net:47823", "dht3.example.net:47823"]
```

```sh
rhexora dht-run --data-dir ./node-c --advertise-host YOUR_PUBLIC_IP \
  --bootstrap-file ./bootstrap.json
# alternatively:
RHEXORA_DHT_BOOTSTRAP='dht1.example.net:47823,dht2.example.net:47823' \
  rhexora dht-run --data-dir ./node-c --advertise-host YOUR_PUBLIC_IP
```

The `health` RPC is a signed, non-mutating reachability probe used by `scripts/public_network_smoke.py`; it does not insert the probe node into the bootstrap's routing table. Public seed deployment is documented in `docs/GLOBAL_DHT.md` and `ops/dht/compose.yaml`.

The DHT refuses to connect to private, loopback and link-local endpoints by default to reduce SSRF and local-network probing risks. For an explicitly trusted private overlay or local lab only, add `--allow-private-endpoints` to each `dht-run` command.

Use actual public addresses and firewall rules in a real deployment. These example addresses are documentation-only and are not live peers. A node behind NAT must not advertise its private LAN endpoint as a globally reachable endpoint.

## Boundaries

- This alpha implements signed-record storage, a 160-bit XOR routing table, ping/announce/find-node UDP RPCs, bootstrap and iterative lookup.
- The DHT is a discovery layer only. It never grants permission to execute tasks and it does not distribute bearer tokens.
- Records are self-asserted endpoints, not proof of reachability. No Sybil resistance, stake/reputation, eclipse resistance, distributed quorum or malicious-peer consensus is implemented.
- IPv4 UDP is implemented. IPv6 support, DHT replication strategy, peer churn simulations, large-scale interoperability and Internet deployment tests remain open. The public network workflow tests only the operator-configured bootstrap endpoints; it does not provide public seed infrastructure.
- UDP records and their signatures are not an independent security audit.
