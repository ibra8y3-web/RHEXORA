# Self-hosted RHEXORA Relay deployment

This container runs only the opaque TCP rendezvous/relay. It does not execute tasks, store submitted task contents, or terminate the worker's inner TLS session.

## 1. Create the relay credential map

On a trusted machine, initialize each Python worker and create a relay credential map:

```bash
rhexora relay-credentials --output ./secrets/relay-credentials.json --node-dir ./worker-a --node-dir ./worker-b
```

For an Android worker, copy the `node_id` and `relay_token` from the private invite into the JSON map as one additional entry. Example shape only (never use these placeholder values):

```json
{
  "worker-node-id-from-private-invite": "relay-token-from-the-same-invite"
}
```

Keep the credential file secret. On a Linux Docker host, grant only the container's unprivileged UID read access:

```bash
sudo chown 10001:10001 ./secrets/relay-credentials.json
sudo chmod 0400 ./secrets/relay-credentials.json
```

## 2. Start the Relay

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f rhexora-relay
```

Open TCP port `47825` on the host firewall and cloud firewall. Use a public DNS name or IP in the workers' relay settings and private invitations. The Relay should be deployed on a host that you control.

## 3. Android Worker

In the Android app, enter the Relay hostname and port `47825`, start the foreground worker, then securely transfer its private invite to the requester. Add that invite's `node_id`/`relay_token` to the Relay credential JSON and restart the container so the updated map is loaded. Do not publish the invite or credential file.

## Security notes

- The container runs as UID/GID `10001`, has a read-only root filesystem, drops Linux capabilities, uses `no-new-privileges`, and mounts the credential file read-only.
- The Relay can observe connection metadata and traffic volume, but task payload bytes remain inside the requester-to-worker TLS session.
- This does not implement ICE or guarantee direct NAT traversal. The Relay is a user-operated fallback, and internet-scale operation needs independent deployment and cross-network testing.
- Rotate a worker's relay token by reinitializing its identity and updating every Relay credential map and private invite that references it.
