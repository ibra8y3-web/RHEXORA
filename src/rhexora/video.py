"""Bounded FFmpeg video transcoding for explicitly shared worker files."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .errors import RemoteTaskError

MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_VIDEO_SECONDS = 30 * 60
_ALLOWED_ENCODERS = {"auto", "libx264", "h264_nvenc", "h264_vaapi", "h264_qsv", "h264_amf", "h264_videotoolbox"}
_HARDWARE_ENCODERS = ("h264_nvenc", "h264_vaapi", "h264_qsv", "h264_amf", "h264_videotoolbox")


def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RemoteTaskError("Video processing exceeded its execution time limit") from exc
    except OSError as exc:
        raise RemoteTaskError(f"Could not start media tool: {exc}") from exc


def _probe_hardware_encoder(ffmpeg: str, encoder: str) -> tuple[bool, str | None]:
    """Run a real one-frame encode, not a device/encoder-name heuristic."""
    base = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=128x128:rate=1", "-frames:v", "1", "-an"]
    if encoder == "h264_nvenc":
        args = base + ["-c:v", encoder, "-preset", "p1", "-f", "null", "-"]
    elif encoder == "h264_vaapi":
        device = "/dev/dri/renderD128"
        if not Path(device).exists():
            return False, "VAAPI render device /dev/dri/renderD128 is not present"
        args = base + ["-vaapi_device", device, "-vf", "format=nv12,hwupload", "-c:v", encoder, "-f", "null", "-"]
    elif encoder == "h264_qsv":
        args = base + ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw", "-vf", "format=nv12,hwupload=extra_hw_frames=16", "-c:v", encoder, "-f", "null", "-"]
    elif encoder == "h264_amf":
        args = base + ["-c:v", encoder, "-quality", "speed", "-f", "null", "-"]
    elif encoder == "h264_videotoolbox":
        args = base + ["-c:v", encoder, "-b:v", "500k", "-f", "null", "-"]
    else:
        return False, "unsupported hardware encoder"
    try:
        result = _run(args, timeout=25)
    except RemoteTaskError as exc:
        return False, str(exc)[-500:]
    if result.returncode == 0:
        return True, None
    return False, (result.stderr or "hardware encode probe failed")[-500:]


def video_capabilities() -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return {"available": False, "ffmpeg": ffmpeg, "ffprobe": ffprobe, "encoders": [], "hardware_encoders": [], "hardware_probe_errors": {}}
    result = _run([ffmpeg, "-hide_banner", "-encoders"], timeout=15)
    if result.returncode != 0:
        return {"available": False, "ffmpeg": ffmpeg, "ffprobe": ffprobe, "encoders": [], "hardware_encoders": [], "hardware_probe_errors": {}}
    encoders = [name for name in ("libx264", *_HARDWARE_ENCODERS) if name in result.stdout]
    hardware_encoders: list[str] = []
    hardware_probe_errors: dict[str, str] = {}
    for name in _HARDWARE_ENCODERS:
        if name not in encoders:
            continue
        success, error = _probe_hardware_encoder(ffmpeg, name)
        if success:
            hardware_encoders.append(name)
        elif error:
            hardware_probe_errors[name] = error
    return {
        "available": "libx264" in encoders,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "encoders": encoders,
        "hardware_encoders": hardware_encoders,
        "hardware_probe_errors": hardware_probe_errors,
        "hardware_verified": bool(hardware_encoders),
        "note": "hardware_encoders contains only encoders that completed a real one-frame hardware encode on this host.",
    }


def _encoder_args(encoder: str, ffmpeg: str, source: Path, output: Path) -> list[str]:
    args = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a?"]
    if encoder == "h264_nvenc":
        args += ["-c:v", encoder, "-preset", "p4", "-cq", "23", "-b:v", "0"]
    elif encoder == "h264_vaapi":
        args += ["-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload", "-c:v", encoder, "-qp", "23"]
    elif encoder == "h264_qsv":
        args += ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw", "-vf", "format=nv12,hwupload=extra_hw_frames=64", "-c:v", encoder, "-global_quality", "23"]
    elif encoder == "h264_amf":
        args += ["-c:v", encoder, "-quality", "balanced", "-qp_i", "23", "-qp_p", "23"]
    elif encoder == "h264_videotoolbox":
        args += ["-c:v", encoder, "-b:v", "4M"]
    else:
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", "23"]
    args += ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output)]
    return args


def _safe_input(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or len(relative) > 512:
        raise RemoteTaskError("input_file must be a relative path inside this worker's shared-media directory")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise RemoteTaskError("Absolute media paths are not accepted")
    try:
        resolved_root = root.resolve(strict=True)
        unresolved = resolved_root / candidate
        cursor = resolved_root
        for part in candidate.parts:
            if part in {"", "."}:
                continue
            cursor = cursor / part
            if cursor.is_symlink():
                raise RemoteTaskError("Symlinks are not accepted in shared-media paths")
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except RemoteTaskError:
        raise
    except (OSError, ValueError):
        raise RemoteTaskError("Input file is missing or outside the shared-media directory")
    if not resolved.is_file():
        raise RemoteTaskError("Input must be a regular file, not a symlink")
    size = resolved.stat().st_size
    if size <= 0 or size > MAX_VIDEO_BYTES:
        raise RemoteTaskError(f"Video file must be between 1 byte and {MAX_VIDEO_BYTES} bytes")
    return resolved


def _probe(ffprobe: str, path: Path) -> dict[str, Any]:
    result = _run([
        ffprobe, "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height",
        "-of", "json", str(path),
    ], timeout=20)
    if result.returncode != 0:
        raise RemoteTaskError("ffprobe could not inspect the video")
    try:
        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        streams = data.get("streams", [])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RemoteTaskError("ffprobe returned invalid media metadata") from exc
    if duration <= 0 or duration > MAX_VIDEO_SECONDS:
        raise RemoteTaskError(f"Video duration must be between 0 and {MAX_VIDEO_SECONDS} seconds")
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video_stream:
        raise RemoteTaskError("Input has no video stream")
    return {"duration_seconds": round(duration, 3), "video_codec": video_stream.get("codec_name"), "width": video_stream.get("width"), "height": video_stream.get("height")}


def transcode_video(payload: dict[str, Any], media_root: str | Path) -> dict[str, Any]:
    """Transcode a worker-shared local file; arbitrary remote paths are never opened."""
    media_path = Path(media_root)
    if media_path.is_symlink():
        raise RemoteTaskError("Worker shared-media directory must not be a symlink")
    input_root = media_path.resolve(strict=True)
    output_root = input_root.parent / "processed-media"
    if output_root.is_symlink():
        raise RemoteTaskError("Worker processed-media directory must not be a symlink")
    output_root.mkdir(parents=True, exist_ok=True)
    # Validate the requested path before probing the machine, so path-traversal
    # attempts are rejected the same way on every worker (with or without FFmpeg).
    source = _safe_input(input_root, payload.get("input_file"))
    caps = video_capabilities()
    if not caps["available"]:
        raise RemoteTaskError("FFmpeg and an H.264 software encoder (libx264) are required on this worker")
    metadata = _probe(str(caps["ffprobe"]), source)
    requested = payload.get("encoder", "auto")
    if requested not in _ALLOWED_ENCODERS:
        raise RemoteTaskError("encoder must be one of: " + ", ".join(sorted(_ALLOWED_ENCODERS)))
    fallback = payload.get("allow_cpu_fallback", True)
    if not isinstance(fallback, bool):
        raise RemoteTaskError("allow_cpu_fallback must be a boolean")

    attempts: list[str] = []
    available_hardware = list(caps["hardware_encoders"])
    if requested == "libx264":
        attempts = ["libx264"]
    elif requested == "auto":
        attempts = available_hardware + ["libx264"]
    elif requested in available_hardware:
        attempts = [requested] + (["libx264"] if fallback else [])
    elif fallback:
        attempts = ["libx264"]
    else:
        detail = caps.get("hardware_probe_errors", {}).get(requested, "encoder is not available or failed its hardware probe")
        raise RemoteTaskError(f"{requested} is not usable on this worker: {detail}")

    job_id = uuid.uuid4().hex
    output_name = f"{job_id}.mp4"
    final_path = output_root / output_name
    temp_path = output_root / f".{job_id}.partial.mp4"
    failures: list[str] = []
    selected: str | None = None
    for encoder in attempts:
        args = _encoder_args(encoder, str(caps["ffmpeg"]), source, temp_path)
        result = _run(args, timeout=900)
        if result.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 0:
            selected = encoder
            break
        failures.append(f"{encoder}: {(result.stderr or 'encoder failed')[-600:]}")
        temp_path.unlink(missing_ok=True)
    if selected is None:
        raise RemoteTaskError("All requested video encoders failed: " + " | ".join(failures))
    os.replace(temp_path, final_path)
    out_meta = _probe(str(caps["ffprobe"]), final_path)
    output_digest = hashlib.sha256()
    with final_path.open("rb") as output_handle:
        for block in iter(lambda: output_handle.read(1024 * 1024), b""):
            output_digest.update(block)
    return {
        "ok": True,
        "output_file": output_name,
        "output_sha256": output_digest.hexdigest(),
        "output_relative_path": f"processed-media/{output_name}",
        "output_bytes": final_path.stat().st_size,
        "encoder": selected,
        "hardware_accelerated": selected in caps["hardware_encoders"],
        "cpu_fallback_used": requested not in {"auto", "libx264"} and selected == "libx264",
        "attempted_encoders": attempts,
        "input": metadata,
        "output": out_meta,
    }
