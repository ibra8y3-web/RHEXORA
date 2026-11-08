# GPU video acceleration

The worker checks FFmpeg's advertised encoders and then runs an actual one-frame hardware encode probe before marking a hardware encoder as usable. Merely listing an encoder in `ffmpeg -encoders` is not treated as proof of an available GPU/device/driver.

Supported H.264 hardware paths where present in the host FFmpeg build:

- NVIDIA NVENC: `h264_nvenc`
- Linux VAAPI: `h264_vaapi` (requires `/dev/dri/renderD128` and device permissions)
- Intel Quick Sync: `h264_qsv`
- AMD AMF: `h264_amf`
- Apple VideoToolbox: `h264_videotoolbox`

Run:

```bash
rhexora media-capabilities
rhexora submit video --data-dir ./requester --peer worker-a --input-file source.mp4 --encoder auto
```

`auto` uses only hardware encoders that passed the real probe and falls back to `libx264`. Selecting a specific hardware encoder with `--no-cpu-fallback` or distributed video requires that encoder to pass the probe; otherwise the job fails rather than silently pretending to use a GPU.

## CI acceptance

The `Real GPU video acceptance` workflow targets a self-hosted GitHub Actions runner labeled `rhexora-gpu`. Register such a runner only on a machine with a GPU, vendor driver, FFmpeg built with the corresponding encoder, and required device permissions. The script generates a real test video and requires each detected hardware encoder to produce a verified H.264 output with CPU fallback disabled. A normal GitHub-hosted runner is not assumed to have a GPU.
