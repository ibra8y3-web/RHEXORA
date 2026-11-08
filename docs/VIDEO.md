# Video processing and distributed transcoding (alpha)

RHEXORA supports two real FFmpeg paths. Both are explicit opt-in features: workers must be started with video enabled, and outputs remain inside managed directories.

## Worker-local transcode

```sh
rhexora init --data-dir ./video-worker --enable-video
# Copy an operator-approved input into ./video-worker/shared-media/
rhexora run --data-dir ./video-worker
```

A trusted peer can request a transcode:

```sh
rhexora submit video --data-dir ./requester --peer video-worker --input-file clip.mp4 --encoder auto --timeout 900
```

## Distributed video pipeline

The requester segments its own input locally, uploads each independently decodable MP4 segment to a trusted worker over the authenticated TLS protocol, asks that worker to run the allow-listed `media.video.transcode` operation, downloads the output in bounded chunks, checks SHA-256, and assembles the outputs with FFmpeg.

```sh
# Start at least two trusted workers with video enabled.
rhexora init --data-dir ./worker-a --enable-video
rhexora run --data-dir ./worker-a
# Repeat for worker-b, then add both private invites to requester peers.

rhexora submit video-distribute \
  --data-dir ./requester \
  --input ./source.mp4 \
  --output ./finished.mp4 \
  --segment-seconds 30 \
  --encoder libx264
```

The distributed pipeline is CPU-first by default. Each worker uses FFmpeg; `h264_nvenc` is available only when explicitly selected, the real one-frame capability probe succeeds, and the worker has compatible NVIDIA hardware/drivers. A listed encoder alone does not prove GPU functionality. Tests in this environment exercise real CPU H.264 encodes, not GPU hardware.

## Safety and limits

- Video processing must be enabled by the worker operator.
- Uploads are authenticated, chunk ordered, size bounded, written under `.incoming`, and SHA-256 verified before the final name is exposed.
- Downloads are restricted to generated output IDs under `processed-media` and verified by the requester.
- Source inputs are bounded to 512 MiB and 30 minutes in the distributed pipeline. A worker's video task is restricted to its own configured shared-media directory.
- This is not arbitrary remote command execution; only the pre-defined transcode task and approved encoders are accepted.
- Distributed segment encoding can change output timing/quality relative to a single-pass encode. The pipeline validates that FFprobe sees a final H.264 video stream, but does not promise frame-identical output or preserve every source metadata track. Review important media manually.
