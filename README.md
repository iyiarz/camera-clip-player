# Camera clip player

Plays the `.media` files that some IP cameras and video doorbells write to their
SD cards — the ones that won't open in VLC, that no codec pack fixes, and that
at least one manufacturer's support line will tell you can't be viewed on a PC.

They can. The video inside is ordinary H.264 and the audio is ordinary G.711.
The only thing standing in the way is an undocumented wrapper around them, which
is [written up in FORMAT.md](FORMAT.md).

**[Open the player](https://iyiarz.github.io/camera-clip-player/)** —
or download `index.html` and double-click it. Nothing is uploaded. The page has
no server, no analytics and no network access at all; your footage is decoded in
the browser and never leaves the machine.

## What it does

Point it at the folder from your SD card and it shows every clip as a thumbnail,
grouped by day, labelled with the time it was recorded. Click one to play it
with sound.

| | |
|---|---|
| Space | play / pause |
| ← / → | skip 5 seconds |
| N / P | next / previous clip |
| Esc | back to the grid |

Needs Chrome or Edge 94 or newer, which is where the WebCodecs API is
dependable. Firefox and Safari added it later and are patchier.

## If you'd rather not use a browser

`tools/` holds two Python scripts. Both need `ffmpeg` on your PATH.

```bash
# Play a clip, or a whole folder, directly
pip install av pygame numpy
python3 tools/mediaplay.py /path/to/folder

# Convert to MP4 instead, without re-encoding the video
python3 tools/media2mp4.py 0012.media
for f in *.media; do python3 tools/media2mp4.py "$f"; done
```

There is also a one-liner that needs nothing but ffmpeg. It works because an
H.264 decoder resynchronises on start codes and simply steps over the wrapper's
bytes as junk. You get picture but no sound, and you must set the frame rate by
hand or it plays at double speed:

```bash
ffplay -f h264 -r 15 0012.media
vlc --demux=h264 --h264-fps=15 0012.media
```

## Does this work with my camera?

If your SD card holds numbered `.media` files — often beside a small JSON
`.info` file — it's worth trying. The player reads the H.264 profile out of the
stream rather than assuming it, so other resolutions and frame rates should work.

Two things will stop it. If the `.info` file says `"encrypt"` is anything other
than `0`, the payload is encrypted and none of this applies. And if a vendor has
changed the header layout, the player will say the file doesn't look right
rather than showing you garbage.

Either way, please open an issue — ideally with the `.info` file and the first
few hundred bytes of a `.media` file, which is enough to identify the layout:

```bash
head -c 512 0012.media | xxd
```

Don't attach whole recordings. They contain footage of your home.

## Contributing

The interesting work is compatibility with more cameras. If you get it working
with a model not listed here, a note in an issue helps the next person find it.

## Licence

MIT. See [LICENSE](LICENSE).
