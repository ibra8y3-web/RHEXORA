# Contributing to RHEXORA

1. Discuss protocol changes before implementing them.
2. Keep the default worker allow-list narrow. Do not add arbitrary shell or Python execution.
3. Add tests for malformed frames, authentication failures, resource limits, and normal task behavior.
4. Never commit node identity files, TLS private keys, invitation files, or real user data.
5. Update `docs/PROTOCOL.md` and `docs/SECURITY.md` for protocol/security changes.
6. Run `PYTHONPATH=src pytest -q` before submitting a pull request.
7. For video code, run the FFmpeg integration tests with FFmpeg and FFprobe installed.
8. For Android code, run `gradle --no-daemon testDebugUnitTest assembleDebug` from `android/` and test on a physical device where possible.
9. Never mark a public-network, GPU, Android-device, or independent-security gate complete without the corresponding real-world validation.

By submitting a contribution, you agree that it is provided under the Apache License 2.0, unless explicitly agreed otherwise by the project maintainers.
