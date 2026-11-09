# RHEXORA Android client and worker

Native Kotlin + Jetpack Compose app with a compact three-destination interface: Home, Tasks and Settings. Arabic right-to-left layout, light/dark/system appearance, visible worker state and Relay configuration are included.

## Tasks available on Android

- `math.count_primes` — bounded to 200,000 integers and a stop value no greater than `10^12`.
- `math.sum_ints` — up to 100,000 signed 64-bit values from the Android UI; the total must also fit signed 64-bit. The worker rejects non-integer values and sum overflow before reserving the request.
- `text.analyze` — Unicode-aware character, word and line counts; the UI caps submitted text at 100,000 characters.

Requester and worker support the same three built-in tasks. Unsupported task types, arbitrary code execution, Android video/FFmpeg and Android GPU work are not available.

## Requester flow

- Paste invitation JSON, choose a JSON/text file, or paste from the clipboard.
- Validate invite version, host/port, bearer token, TLS certificate fingerprint and optional Relay fields before enabling task execution.
- Connect directly or through the invitation's self-hosted opaque TCP Relay. The requester pins the worker's leaf-certificate SHA-256 fingerprint on both paths.
- Check protocol version, request ID, invited node ID, task hash and result hash before showing a result.
- Copy a task summary without copying invitation secrets.

## Android worker

- The device owner explicitly starts and stops a foreground service with an ongoing notification; it is not started automatically at boot.
- Uses a persistent RSA-2048 TLS identity in the app-private sandbox. The private key is stored in a PKCS#12 file protected by a random app-private password. App backup is disabled.
- Listens on TCP port `47821`, authenticates the invitation bearer token, and accepts only the three bounded tasks listed above.
- Uses a segmented sieve for prime counting, bounded integer addition and Unicode text statistics.
- Keeps a 14-day SQLite idempotency ledger for completed request IDs. At service startup, unfinished `running` entries are marked interrupted so a retry can reclaim and rerun the bounded task; a crash after calculation but before response persistence can still cause recomputation.
- Can maintain an outbound HMAC-authenticated connection to a configured Relay host for phones behind NAT/CGNAT. The requester-to-worker TLS session remains end-to-end through the opaque Relay. Add the invite's `node_id` and `relay_token` to the Relay operator's credential map before using it.
- Uses a bounded thread pool and rejects excess concurrent connections rather than allowing an unbounded request queue.

## Settings and security

- Relay host and port are saved in Android app-private preferences. The remote Relay default is `47825`; the worker listener on the phone is `47821`. Changing Relay settings while the worker runs requires a restart to apply.
- Relay configuration does not prove that a Relay is deployed or reachable. The operator must configure its credential map and provide a public endpoint.
- Invitation JSON contains bearer tokens and certificate identity data. Treat it like a password, share only with a trusted peer, and do not commit or post it.
- The app does not offer token revocation or identity rotation. Clearing app data may erase the worker identity and invalidate prior invites.

Android does not yet support WebRTC ICE, peer/DHT discovery or video/GPU tasks. Foreground-service, battery saver, thermal, OEM process-management and physical-network behavior vary by device and remain subject to real-device testing. The service does not hold a permanent CPU wake lock. The release APK is unsigned.

## Build and test

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

GitHub Actions builds debug and unsigned release APKs and runs Android unit tests. Passing CI verifies the source build only; physical-device validation remains necessary.
