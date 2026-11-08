"""Authenticated distributed video segmentation, worker encoding and assembly."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .client import Peer, RhexoraClient
from .errors import ConfigurationError, RemoteTaskError

MAX_SOURCE_BYTES = 512 * 1024 * 1024


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RemoteTaskError("Distributed video stage exceeded its time limit") from exc
    except OSError as exc:
        raise RemoteTaskError(f"Could not start FFmpeg: {exc}") from exc
    if result.returncode != 0:
        raise RemoteTaskError((result.stderr or "FFmpeg failed")[-2000:])
    return result


def _duration(source: Path, ffprobe: str) -> float:
    result = _run([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(source),
    ], 30)
    try:
        value = float(result.stdout.strip())
    except ValueError as exc:
        raise RemoteTaskError("Could not read source video duration") from exc
    if not (0 < value <= 1800):
        raise RemoteTaskError("Source video must be longer than zero and no longer than 30 minutes")
    return value


def _make_segments(source: Path, directory: Path, ffmpeg: str, segment_seconds: int, minimum: int) -> list[Path]:
    pattern = directory / "source-%05d.mp4"
    base = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?", "-f", "segment", "-segment_time", str(segment_seconds),
            "-reset_timestamps", "1", "-segment_format", "mp4"]
    # Fast path: split only at existing keyframes, avoiding an extra local encode pass.
    _run(base[:base.index("-f")] + ["-c", "copy"] + base[base.index("-f"): ] + [str(pattern)], 300)
    segments = sorted(directory.glob("source-*.mp4"))
    if len(segments) >= minimum:
        return segments
    for item in segments:
        item.unlink(missing_ok=True)
    # If the source keyframe spacing prevents useful parallelism, create independently decodable
    # segments with forced boundaries. This is a CPU preparation pass; final encoding is remote.
    gop = max(1, segment_seconds * 2)
    args = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
        "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
        "-force_key_frames", f"expr:gte(t,n_forced*{segment_seconds})",
        "-c:a", "aac", "-f", "segment", "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1", "-segment_format", "mp4", str(pattern),
    ]
    _run(args, 900)
    segments = sorted(directory.glob("source-*.mp4"))
    if not segments:
        raise RemoteTaskError("FFmpeg did not create any independently processable video segments")
    return segments


async def distributed_video_transcode(
    source: str | Path,
    output: str | Path,
    peers: list[Peer],
    *,
    segment_seconds: int = 30,
    encoder: str = "libx264",
    timeout: float = 1800,
    max_in_flight: int = 4,
) -> dict[str, Any]:
    """Upload, process and download real video segments on trusted worker nodes.

    Segment preparation is local. Every resulting segment is uploaded using the authenticated
    TLS media-transfer protocol, encoded remotely, downloaded and SHA-256 verified before muxing.
    """
    src = Path(source).expanduser().resolve(strict=True)
    dest = Path(output).expanduser().resolve()
    if not src.is_file() or src.is_symlink():
        raise ValueError("Source must be a regular non-symlink file")
    if src.stat().st_size <= 0 or src.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source video must be between 1 byte and 512 MiB")
    if src == dest:
        raise ValueError("Output path must be different from the source path")
    if not peers:
        raise ConfigurationError("At least one trusted media worker is required")
    if not 2 <= segment_seconds <= 300:
        raise ValueError("segment_seconds must be between 2 and 300")
    if encoder not in {"libx264", "h264_nvenc", "h264_vaapi", "h264_qsv", "h264_amf", "h264_videotoolbox"}:
        raise ValueError("Distributed video encoder must be a supported H.264 FFmpeg encoder")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RemoteTaskError("FFmpeg and FFprobe are required for distributed video processing")
    duration = _duration(src, ffprobe)
    dest.parent.mkdir(parents=True, exist_ok=True)
    client = RhexoraClient(timeout=timeout, max_in_flight=max_in_flight)

    with tempfile.TemporaryDirectory(prefix="rhexora-video-") as temporary:
        temp = Path(temporary)
        source_segments = _make_segments(src, temp, ffmpeg, segment_seconds, min(2, len(peers)))
        output_segments = [temp / f"encoded-{index:05d}.mp4" for index in range(len(source_segments))]
        semaphore = asyncio.Semaphore(max_in_flight)

        async def process(index: int, segment: Path) -> dict[str, Any]:
            peer = peers[index % len(peers)]
            async with semaphore:
                uploaded = await client.upload_media(peer, segment)
                task = await client.execute(peer, "media.video.transcode", {
                    "input_file": uploaded["input_file"],
                    "encoder": encoder,
                    "allow_cpu_fallback": False,
                })
                result = task["result"]
                local_output = await client.download_media(
                    peer,
                    result["output_file"],
                    output_segments[index],
                    expected_size=int(result["output_bytes"]),
                    expected_sha256=str(result["output_sha256"]),
                )
                return {
                    "segment": index,
                    "worker": peer.node_id or peer.name,
                    "input_bytes": uploaded["size"],
                    "output_bytes": local_output.stat().st_size,
                    "encoder": result["encoder"],
                    "hardware_accelerated": bool(result.get("hardware_accelerated")),
                }

        segment_results = await asyncio.gather(*(
            process(index, segment) for index, segment in enumerate(source_segments)
        ))
        concat_list = temp / "concat.txt"
        lines = []
        for path in output_segments:
            escaped = str(path).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # All pieces use the same requested codec settings on their workers.
        try:
            _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                  "-f", "concat", "-safe", "0", "-i", str(concat_list),
                  "-c", "copy", "-movflags", "+faststart", str(dest)], 900)
        except RemoteTaskError:
            # Different FFmpeg builds may produce incompatible MP4 time bases; normalize during mux.
            _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                  "-f", "concat", "-safe", "0", "-i", str(concat_list),
                  "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                  "-c:a", "aac", "-movflags", "+faststart", str(dest)], 900)

    probe = _run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
                  "-of", "json", str(dest)], 30)
    media = json.loads(probe.stdout)
    video = next((x for x in media.get("streams", []) if x.get("codec_type") == "video"), None)
    if not video or video.get("codec_name") != "h264":
        dest.unlink(missing_ok=True)
        raise RemoteTaskError("Final assembled output failed H.264 verification")
    return {
        "ok": True,
        "output": str(dest),
        "output_bytes": dest.stat().st_size,
        "duration_seconds_source": round(duration, 3),
        "segments_completed": len(source_segments),
        "workers": sorted({str(item["worker"]) for item in segment_results}),
        "segments": segment_results,
        "output_video": video,
        "assembly": "ffmpeg-concat-demuxer",
    }
