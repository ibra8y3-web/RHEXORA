# RHEXORA Android client and worker

Native Kotlin + Jetpack Compose Android application. It can act as both a task requester and an explicitly enabled compute worker.

## Requester client

- Paste a private version-1 invite JSON created by `rhexora invite`.
- Establish TLS and pin the peer leaf-certificate SHA-256 fingerprint.
- Connect directly or through the self-hosted opaque TCP relay named in the invite.
- Send a real `math.count_primes` task and validate protocol version, request ID, invited node ID, task hash and result hash.

## Android worker service

- The owner explicitly starts/stops the service from the app; it is not auto-started at boot.
- Uses an Android foreground service with a visible ongoing notification and requests notification permission on Android 13+.
- The service has its own persistent RSA-2048 TLS identity in the app-private sandbox. The private key is stored in a PKCS#12 file protected by a randomly generated app-private password. App backup is disabled.
- Listens on TCP port `47821`, authenticates the invitation bearer token, and only accepts `math.count_primes` tasks.
- Uses a segmented sieve, with a maximum range of 200,000 integers and exclusive stop at or below `10^12`.
- Keeps a 14-day SQLite idempotency ledger for completed request IDs. At service startup, unfinished `running` entries are marked interrupted so a retry can reclaim and rerun the bounded task; a crash after calculation but before response persistence can still cause recomputation.
- Can maintain an outbound HMAC-authenticated connection to a configured Relay host for phones behind NAT/CGNAT. The requester-to-worker TLS session remains end-to-end through the opaque Relay. Add the invite's `node_id` and `relay_token` to the Relay operator's credential map before using it.
- Uses a bounded thread pool and rejects excess concurrent connections rather than allowing an unbounded request queue.

The Android worker currently supports only prime-count tasks; it does not yet accept media uploads or run FFmpeg/GPU video jobs. Android foreground-service, battery saver, thermal, OEM process-management, and physical-network behavior vary by device and have not yet been tested on real phones. The service does not hold a permanent CPU wake lock; Android may suspend work under device power policies.

The service is declared as `connectedDevice`, since it maintains a network interaction with remote peer devices. Android requires the foreground-service type permission plus an eligible network permission for this use case. Confirm store-policy eligibility before Play distribution. See [Android foreground service types](https://developer.android.com/develop/background-work/services/fgs/service-types). 

## Build

Requirements: JDK 17, Android SDK Platform 35, Gradle 8.9.

```sh
cd android
gradle --no-daemon testDebugUnitTest assembleDebug assembleRelease
```

Artifacts:

```text
app/build/outputs/apk/debug/app-debug.apk
app/build/outputs/apk/release/app-release-unsigned.apk
```

The release APK is unsigned and is not suitable for distribution until signed with a managed release key. GitHub Actions builds both APKs and runs Android unit tests. CI must pass before considering the Android source build-verified; a physical-device matrix is still required.

## Invite handling

Invite JSON contains a bearer token, TLS fingerprint, and (when Relay is enabled) a separate Relay token. Keep it private and transfer it over a secure channel. The app does not log invite tokens. Direct mode requires the endpoint to be reachable. Relay mode requires a public reachable Relay and an exact matching `node_id`/`relay_token` entry in its credential map.
