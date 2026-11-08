#!/usr/bin/env python3
"""Strict hardware video acceptance test. Exits non-zero if no real GPU encode works."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rhexora.video import transcode_video, video_capabilities


def main() -> int:
    caps = video_capabilities()
    print(json.dumps({key: value for key, value in caps.items() if key != "hardware_probe_errors"}, indent=2))
    if not caps.get("hardware_encoders"):
        print("FAIL: No hardware encoder passed a real FFmpeg encode probe on this runner.", file=sys.stderr)
        print(json.dumps(caps.get("hardware_probe_errors", {}), indent=2), file=sys.stderr)
        return 2
    ffmpeg = caps["ffmpeg"]
    with tempfile.TemporaryDirectory(prefix="rhexora-gpu-acceptance-") as temp:
        root = Path(temp)
        shared = root / "shared-media"
        shared.mkdir()
        source = shared / "input.mp4"
        generated = subprocess.run([
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-t", "2",
            "-pix_fmt", "yuv420p", str(source),
        ], capture_output=True, text=True, timeout=60)
        if generated.returncode != 0:
            print(generated.stderr, file=sys.stderr)
            return 3
        verified = []
        for encoder in caps["hardware_encoders"]:
            result = transcode_video({"input_file": "input.mp4", "encoder": encoder, "allow_cpu_fallback": False}, shared)
            if result["encoder"] != encoder or not result["hardware_accelerated"]:
                raise RuntimeError(f"{encoder} did not produce a verified hardware encode")
            verified.append({"encoder": encoder, "output_bytes": result["output_bytes"], "sha256": result["output_sha256"]})
        print(json.dumps({"status": "passed", "verified_hardware_encodes": verified}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
