#!/usr/bin/env python3
"""
Convert proprietary camera .media files to playable MP4.

Container format (reverse-engineered):
  Repeating chunks, each with a 24-byte little-endian header:
    uint32 type      0 = video P-frame, 1 = video keyframe (SPS/PPS/IDR), 3 = audio
    uint32 length    payload length in bytes
    uint64 ts_wall   wall-clock timestamp, milliseconds
    uint64 ts_media  media timestamp (video: microseconds, audio: milliseconds)
  Payload:
    video -> raw H.264 Annex-B (start codes already present, just concatenate)
    audio -> G.711 mu-law, 8 kHz mono, 160 bytes = 20 ms per chunk

Usage:  python3 media2mp4.py 0012.media [output.mp4]
"""
import struct, subprocess, sys, os, tempfile

VIDEO_TYPES = {0, 1}
AUDIO_TYPE = 3
HDR = struct.Struct("<IIQQ")


def demux(path):
    data = open(path, "rb").read()
    video, audio, off = [], [], 0
    while off + HDR.size <= len(data):
        ctype, length, _ts_wall, ts_media = HDR.unpack_from(data, off)
        payload = data[off + HDR.size: off + HDR.size + length]
        if len(payload) != length:
            print(f"warning: truncated chunk at offset {off}", file=sys.stderr)
            break
        if ctype in VIDEO_TYPES:
            video.append((ts_media, payload))
        elif ctype == AUDIO_TYPE:
            audio.append((ts_media, payload))
        off += HDR.size + length
    if off != len(data):
        print(f"warning: {len(data) - off} trailing bytes not parsed", file=sys.stderr)
    return video, audio


def fps_from(video, default=15.0):
    if len(video) < 2:
        return default
    span = (video[-1][0] - video[0][0]) / 1e6  # microseconds -> seconds
    return round(len(video) / span, 2) if span > 0 else default


def convert(src, dst=None):
    dst = dst or os.path.splitext(src)[0] + ".mp4"
    video, audio = demux(src)
    if not video:
        sys.exit("No video frames found - container format may differ.")
    fps = fps_from(video)
    print(f"{len(video)} video frames, {len(audio)} audio frames, ~{fps} fps")

    with tempfile.TemporaryDirectory() as tmp:
        vpath = os.path.join(tmp, "v.h264")
        apath = os.path.join(tmp, "a.ul")
        open(vpath, "wb").write(b"".join(p for _, p in video))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-r", str(fps), "-i", vpath]
        if audio:
            open(apath, "wb").write(b"".join(p for _, p in audio))
            cmd += ["-f", "mulaw", "-ar", "8000", "-ac", "1", "-i", apath,
                    "-c:a", "aac", "-b:a", "64k", "-ar", "44100", "-shortest"]
        cmd += ["-c:v", "copy", "-movflags", "+faststart", dst]
        subprocess.run(cmd, check=True)
    print(f"wrote {dst}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    convert(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
