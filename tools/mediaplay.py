#!/usr/bin/env python3
"""
mediaplay - play camera .media files directly, with sound. No conversion.

Parses the proprietary chunk container in memory and feeds the H.264 and
G.711 payloads straight to the decoder. Nothing is written to disk.

    pip install av pygame numpy
    python3 mediaplay.py 0012.media
    python3 mediaplay.py /path/to/folder      # plays every .media in order

Keys:
    Space       pause / resume
    Left/Right  seek back / forward 5 seconds
    N / P       next / previous file
    R           restart current file
    F           toggle fullscreen
    Q or Esc    quit
"""

import os
import struct
from collections import deque
import sys
import time

import av
import numpy as np
import pygame

HDR = struct.Struct("<IIQQ")        # type, length, wall-clock ms, media ts
TYPE_PFRAME, TYPE_KEYFRAME, TYPE_AUDIO = 0, 1, 3
AUDIO_RATE = 8000
SEEK_STEP = 5.0


# --- G.711 mu-law -> signed 16-bit PCM, via a 256-entry lookup table ---------
def _build_mulaw_table():
    table = np.zeros(256, dtype=np.int16)
    for i in range(256):
        u = ~i & 0xFF
        mantissa = u & 0x0F
        exponent = (u >> 4) & 0x07
        sign = u & 0x80
        magnitude = ((mantissa << 3) + 0x84) << exponent
        sample = magnitude - 0x84
        table[i] = -sample if sign else sample
    return table


MULAW = _build_mulaw_table()


class MediaFile:
    """Parsed container: video packets with timestamps, plus decoded audio."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        data = open(path, "rb").read()

        video, audio_chunks = [], []
        off = 0
        while off + HDR.size <= len(data):
            ctype, length, _wall, ts = HDR.unpack_from(data, off)
            payload = data[off + HDR.size: off + HDR.size + length]
            if len(payload) != length:
                break                                  # truncated tail
            if ctype in (TYPE_PFRAME, TYPE_KEYFRAME):
                video.append((ts / 1e6, ctype == TYPE_KEYFRAME, payload))
            elif ctype == TYPE_AUDIO:
                audio_chunks.append((ts / 1e3, payload))
            off += HDR.size + length

        if not video:
            raise ValueError(f"no video found in {self.name}")

        # Video and audio clocks start a few ms apart; align both to t0.
        t0 = min(video[0][0], audio_chunks[0][0] if audio_chunks else video[0][0])
        self.video = [(ts - t0, key, payload) for ts, key, payload in video]
        self.keyframes = [ts for ts, key, _ in self.video if key] or [0.0]

        if audio_chunks:
            pcm = MULAW[np.frombuffer(b"".join(p for _, p in audio_chunks),
                                      dtype=np.uint8)]
            pad = int(round((audio_chunks[0][0] - t0) * AUDIO_RATE))
            if pad > 0:
                pcm = np.concatenate([np.zeros(pad, dtype=np.int16), pcm])
            self.audio = np.ascontiguousarray(pcm)
        else:
            self.audio = np.zeros(0, dtype=np.int16)

        self.duration = max(self.video[-1][0], len(self.audio) / AUDIO_RATE)
        self.width, self.height = 0, 0                 # filled on first frame

    def frames_from(self, start_index):
        """Decode forward from a packet index, yielding (pts, rgb_bytes, w, h).

        The H.264 parser buffers a packet before emitting it, so decoded frames
        lag their input chunk. Every chunk holds exactly one frame and the
        stream has no B-frames, so decode order matches display order and a
        FIFO maps each frame back to its own timestamp.
        """
        ctx = av.CodecContext.create("h264", "r")
        pending_pts = deque()

        def emit(frame):
            pts = pending_pts.popleft() if pending_pts else self.video[-1][0]
            return pts, frame.to_ndarray(format="rgb24").tobytes(), \
                frame.width, frame.height

        for pts, _key, payload in self.video[start_index:]:
            pending_pts.append(pts)
            for pkt in ctx.parse(payload):
                for frame in ctx.decode(pkt):
                    yield emit(frame)
        for pkt in ctx.parse(b""):                     # flush parser
            for frame in ctx.decode(pkt):
                yield emit(frame)
        for frame in ctx.decode(None):                 # flush decoder
            yield emit(frame)

    def keyframe_index_at(self, t):
        """Packet index of the last keyframe at or before time t."""
        best = 0
        for i, (pts, key, _) in enumerate(self.video):
            if key and pts <= t:
                best = i
        return best


class Player:
    def __init__(self, paths):
        self.paths = paths
        self.index = 0
        self.fullscreen = False
        self.surface = None

        pygame.init()
        self.audio_ok = True
        try:
            pygame.mixer.init(frequency=AUDIO_RATE, size=-16, channels=1,
                              buffer=1024)
        except pygame.error:
            self.audio_ok = False                      # no sound device
        self.font = pygame.font.SysFont(None, 22)
        self.screen = pygame.display.set_mode((960, 540), pygame.RESIZABLE)
        self.load(0)

    # --- clock --------------------------------------------------------------
    def now(self):
        if self.paused:
            return self.clock_base
        return self.clock_base + (time.perf_counter() - self.clock_start)

    def set_clock(self, t):
        self.clock_base = t
        self.clock_start = time.perf_counter()

    # --- file / seek --------------------------------------------------------
    def load(self, index):
        self.index = index % len(self.paths)
        self.media = MediaFile(self.paths[self.index])
        pygame.display.set_caption(f"{self.media.name}  -  mediaplay")
        self.paused = False
        self.seek(0.0)

    def seek(self, t):
        t = max(0.0, min(t, self.media.duration))
        start = self.media.keyframe_index_at(t)
        self.frames = self.media.frames_from(start)
        self.pending = None
        self.set_clock(t)
        if self.audio_ok and len(self.media.audio):
            pygame.mixer.stop()
            offset = int(t * AUDIO_RATE)
            tail = self.media.audio[offset:]
            if len(tail):
                self.sound = pygame.mixer.Sound(buffer=tail.tobytes())
                if not self.paused:
                    self.sound.play()
        # Drop frames that precede the seek target, then show the first one
        # immediately so the picture updates even while paused.
        while True:
            frame = next(self.frames, None)
            if frame is None or frame[0] >= t - 0.001:
                self.pending = frame
                break
        if self.pending is not None:
            self.show(self.pending)
            self.pending = next(self.frames, None)
        self.draw(force=True)

    def show(self, frame):
        _pts, rgb, w, h = frame
        self.surface = pygame.image.frombuffer(rgb, (w, h), "RGB")

    def toggle_pause(self):
        if self.paused:
            self.paused = False
            self.set_clock(self.clock_base)
            if self.audio_ok and getattr(self, "sound", None):
                self.seek(self.clock_base)             # restart audio in sync
        else:
            self.clock_base = self.now()
            self.paused = True
            if self.audio_ok:
                pygame.mixer.pause()

    # --- drawing ------------------------------------------------------------
    def draw(self, force=False):
        if getattr(self, "surface", None) is None:
            return
        win_w, win_h = self.screen.get_size()
        vid_w, vid_h = self.surface.get_size()
        scale = min(win_w / vid_w, win_h / vid_h)
        size = (max(1, int(vid_w * scale)), max(1, int(vid_h * scale)))
        self.screen.fill((0, 0, 0))
        scaled = pygame.transform.smoothscale(self.surface, size)
        self.screen.blit(scaled, ((win_w - size[0]) // 2, (win_h - size[1]) // 2))

        label = (f"{self.now():5.1f} / {self.media.duration:.1f}s   "
                 f"[{self.index + 1}/{len(self.paths)}] {self.media.name}"
                 f"{'   PAUSED' if self.paused else ''}"
                 f"{'   (no audio device)' if not self.audio_ok else ''}")
        text = self.font.render(label, True, (255, 255, 255))
        shadow = self.font.render(label, True, (0, 0, 0))
        self.screen.blit(shadow, (13, win_h - 26))
        self.screen.blit(text, (12, win_h - 27))
        pygame.display.flip()

    # --- main loop ----------------------------------------------------------
    def run(self):
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.VIDEORESIZE:
                    self.screen = pygame.display.set_mode(event.size,
                                                          pygame.RESIZABLE)
                    self.draw(force=True)
                elif event.type == pygame.KEYDOWN:
                    k = event.key
                    if k in (pygame.K_q, pygame.K_ESCAPE):
                        running = False
                    elif k == pygame.K_SPACE:
                        self.toggle_pause()
                    elif k == pygame.K_LEFT:
                        self.seek(self.now() - SEEK_STEP)
                    elif k == pygame.K_RIGHT:
                        self.seek(self.now() + SEEK_STEP)
                    elif k == pygame.K_r:
                        self.seek(0.0)
                    elif k == pygame.K_n:
                        self.load(self.index + 1)
                    elif k == pygame.K_p:
                        self.load(self.index - 1)
                    elif k == pygame.K_f:
                        self.fullscreen = not self.fullscreen
                        flags = pygame.FULLSCREEN if self.fullscreen else pygame.RESIZABLE
                        self.screen = pygame.display.set_mode((0, 0) if self.fullscreen
                                                              else (960, 540), flags)

            if not self.paused:
                t = self.now()
                # Show every frame whose time has arrived; skip if we fell behind.
                while self.pending is not None and self.pending[0] <= t:
                    self.show(self.pending)
                    self.pending = next(self.frames, None)
                if self.pending is None and t >= self.media.duration:
                    if len(self.paths) > 1:
                        self.load(self.index + 1)
                    else:
                        self.paused = True
                        self.clock_base = self.media.duration

            self.draw()
            time.sleep(0.004)

        pygame.quit()


def collect(args):
    paths = []
    for a in args:
        if os.path.isdir(a):
            paths += [os.path.join(a, f) for f in sorted(os.listdir(a))
                      if f.lower().endswith(".media")]
        else:
            paths.append(a)
    return paths


if __name__ == "__main__":
    files = collect(sys.argv[1:])
    if not files:
        sys.exit(__doc__)
    Player(files).run()
