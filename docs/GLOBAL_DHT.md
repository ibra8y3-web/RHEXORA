# Global DHT bootstrap deployment

The UDP Kademlia-style DHT is implemented in `src/rhexora/dht.py`. Signed sender records are checked against the observed source IP and each source IP is subject to a bounded token-bucket request limit; these controls do not replace an external adversarial audit. Local overlay tests do not prove global reachability. A real public network needs operator-owned seed nodes. This repository intentionally does not ship fake or invented seed addresses.

## Deploy a public bootstrap node

Run the included container on a VPS with a stable public IP/DNS name. Prepare the persistent data directory for the unprivileged container user, and allow inbound/outbound UDP 47823 in both provider and host firewalls:

```bash
cd ops/dht
mkdir -p data
sudo chown 10001:10001 data
DHT_ADVERTISE_HOST=dht1.example.net docker compose up --build -d
```

Deploy at least three seed nodes on different hosts/providers and give each a persistent data volume. Do not deploy all seeds behind one VPS/provider. Bring them into the same overlay in stages: start seed 1 without bootstrap peers; start seed 2 with `RHEXORA_DHT_BOOTSTRAP=dht1.example.net:47823`; start seed 3 with `RHEXORA_DHT_BOOTSTRAP=dht1.example.net:47823,dht2.example.net:47823`. The Compose file passes this variable into the node. Protect the VPS, monitor UDP abuse, and rotate seed nodes as needed. Public bootstrap nodes are discovery infrastructure, not trusted task workers.

## Join the public overlay

Use the list of seed endpoints you control or have independently verified:

```bash
export RHEXORA_DHT_BOOTSTRAP='dht1.example.net:47823,dht2.example.net:47823,dht3.example.net:47823'
rhexora dht-run --data-dir ./dht-node --bind-host 0.0.0.0 \
  --advertise-host YOUR_PUBLIC_IP_OR_DNS --port 47823
```

Or create a JSON file containing a list of `HOST:PORT` strings and use `--bootstrap-file ./bootstrap.json`. The DHT node must be reachable on UDP at its advertised port for peers to route through it. Configure router port forwarding when operating behind a NAT; an STUN-observed address alone is not proof that inbound UDP is reachable.

## External health test

Set the repository variable `RHEXORA_DHT_BOOTSTRAP` to comma-separated public `HOST:PORT` seed endpoints and run the `Public network smoke tests` workflow. The strict check sends a signed, non-mutating DHT health request with a target ID, obtains signed routing neighbors, and health-checks at least one returned neighbor. The probe uses its STUN-mapped public IP in its signed sender record; `health` requests do not insert the GitHub runner into a seed's routing table.

## Production hardening still required

The current implementation is a useful signed-record DHT prototype. Source-IP binding and per-source token buckets are implemented, but they are only basic ingress controls. Before 1.0 production use, it still needs independent protocol review, Sybil/eclipse resistance, application-level DoS controls, routing-table persistence/refresh strategy validation under churn, IPv6 interoperability tests, and a measured large-network simulation/deployment. A few seed nodes do not make a network resilient by themselves.
