import shutil
import subprocess
from pathlib import Path

import pytest

from rhexora.errors import RemoteTaskError
from rhexora.video import transcode_video, video_capabilities


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools are not installed")
def test_real_video_transcode_and_probe(tmp_path: Path):
    caps = video_capabilities()
    if not caps["available"]:
        pytest.skip("No libx264 software encoder")
    data_dir = tmp_path / "node"
    media = data_dir / "shared-media"
    media.mkdir(parents=True)
    source = media / "source.mp4"
    result = subprocess.run([
        shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10", "-t", "1", "-pix_fmt", "yuv420p", str(source)
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    result_data = transcode_video({"input_file": "source.mp4", "encoder": "auto"}, media)
    output = data_dir / result_data["output_relative_path"]
    assert output.is_file() and output.stat().st_size > 0
    assert result_data["encoder"] in {"libx264", "h264_nvenc", "h264_vaapi", "h264_qsv", "h264_amf", "h264_videotoolbox"}
    assert result_data["output"]["video_codec"] == "h264"
    assert result_data["output"]["width"] == 160


def test_video_rejects_path_traversal(tmp_path: Path):
    media = tmp_path / "shared-media"
    media.mkdir()
    with pytest.raises(RemoteTaskError, match="outside"):
        transcode_video({"input_file": "../secrets.txt", "encoder": "libx264"}, media)


def test_hardware_encoder_arguments_are_not_cpu_arguments():
    from rhexora.video import _encoder_args

    from pathlib import Path
    args = _encoder_args("h264_nvenc", "/usr/bin/ffmpeg", Path("in.mp4"), Path("out.mp4"))
    assert "h264_nvenc" in args
    assert "libx264" not in args
    args = _encoder_args("h264_vaapi", "/usr/bin/ffmpeg", Path("in.mp4"), Path("out.mp4"))
    assert "-vaapi_device" in args
    assert any("hwupload" in item for item in args)
