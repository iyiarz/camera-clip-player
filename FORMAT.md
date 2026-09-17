# The `.media` container

Reverse-engineered from a 720p camera recording. Everything below was verified
against a real file; where something is an inference rather than a measurement,
it says so.

## Shape of the file

A flat sequence of chunks. No header, no index, no footer — which is why players
can't identify it. Each chunk is a 24-byte little-endian header followed by its
payload, and the next chunk begins immediately after.

| Offset | Type | Meaning |
|-------:|------|---------|
| 0 | `uint32` | chunk type |
| 4 | `uint32` | payload length in bytes |
| 8 | `uint64` | wall-clock time, Unix milliseconds |
| 16 | `uint64` | media time — video in microseconds, audio in milliseconds |
| 24 | bytes | payload |

Chunk types:

| Value | Payload |
|------:|---------|
| 0 | H.264 P-frame |
| 1 | H.264 keyframe |
| 3 | G.711 µ-law audio, 8 kHz mono |

A correct parser consumes the file exactly, with no trailing bytes. That is the
quickest way to confirm the layout on an unfamiliar file: if you finish with
bytes left over, or hit an implausible length, the layout differs.

## Video

Annex-B H.264 with start codes already present, so the payloads need no
conversion — concatenating them gives a playable elementary stream.

Each chunk holds exactly one complete access unit. In the sample, every one of
the four keyframe chunks contained SPS, PPS and IDR together, and all 176
P-frame chunks contained a single slice NAL and nothing else. Two consequences
worth knowing:

- Any keyframe chunk decodes standalone, because it carries its own parameter
  sets. Thumbnails and seeking both rely on this.
- The parameter sets repeat at every keyframe, so a decoder can be configured
  from any of them rather than only from the start of the file.

There were no B-frames, so decode order matched display order. Don't assume this
holds universally — a camera using B-frames would need reordering by PTS.

Read the profile and level from the SPS rather than hardcoding a codec string.
For WebCodecs that means `avc1.` followed by bytes 1–3 of the SPS NAL, which
gave `avc1.64001f` here: High profile, level 3.1.

## Audio

G.711 µ-law, 8 kHz, mono, 16 bits after decoding. Each chunk is exactly 160
bytes, which is 20 ms.

To distinguish µ-law from A-law, look at the byte histogram: silence is `0xFF`
in µ-law and `0xD5` in A-law. Here `0xFF` was overwhelmingly the most common
byte.

Decoding is a 256-entry lookup table and nothing more. Both implementations in
this repository were checked sample-for-sample against ffmpeg's `pcm_mulaw`
decoder across 96,000 samples: identical, maximum difference zero.

## Timing

Use the timestamps; don't assume a fixed frame rate. In the sample, intervals
between frames ranged from 57 ms to 121 ms around a nominal 15 fps, so the
camera drops frames under load. Playing at a constant rate drifts slowly against
the camera's own burned-in clock.

Video and audio clocks do not start together — audio began 50 ms after video
here. Align both to the earlier of the two and pad the audio with silence,
otherwise they drift apart by that amount for the whole clip.

The wall-clock field at offset 8 is the recording date and time, as a genuine
Unix millisecond epoch in UTC. Checked against the timestamp burned into the
picture: the overlay ran 20:16:44 to 20:16:56 local while the header ran
19:16:45.797 to 19:16:57.778 UTC, the same clock in BST. The header runs about
1.8 s ahead of the overlay, consistent with it recording when a chunk was
written rather than when the frame was captured. Treat it as accurate to a
couple of seconds, not to the frame.

Reading the first and last chunk's wall-clock values gives a clip's duration
without decoding anything.

## The `.info` file

JSON sitting beside the recordings. The sample:

```json
{
  "version": 4, "eventType": 0, "videoType": 0,
  "codec": 2, "frame_rate": 15, "gop_size": 15,
  "video_width": 1280, "video_height": 720,
  "auido_codec": 105, "audio_sample": 8000,
  "audio_databits": 16, "audio_channel": 0,
  "uuid": "…", "encrypt": 0, "security_level": 3,
  "iv": "AAAAAAAAAAAAAAAAAAAAAA==",
  "hash": "…"
}
```

`codec: 2` is H.264 and `auido_codec: 105` is G.711 µ-law — inferred from the
matching streams, not from any documentation. The misspelling of `auido_codec`
is in the original.

`encrypt: 0` is the field that matters most. It was zero here and the payloads
were plainly unencrypted. A non-zero value alongside that `iv` would mean AES,
and nothing in this repository would apply.

Every field that could be cross-checked against the streams agreed: dimensions,
frame rate, sample rate. `gop_size: 15` did not — keyframes appeared every 45
frames, not every 15.

The metadata isn't needed to play a file. Everything required is derivable from
the chunks themselves, which is what both players do, so a clip still plays if
its `.info` file is missing.

## Things to be careful about

**Truncated final chunks.** The last file written before a card is pulled is
often cut mid-chunk. Stop at the last complete chunk rather than throwing.

**Parser lag.** Feeding chunks to an H.264 parser that buffers a packet before
emitting it will pair each decoded frame with the wrong chunk's timestamp,
putting audio permanently one frame out. Queue the timestamps and pop them as
frames emerge.

**Validating input.** A file of zeros parses as an endless run of empty video
chunks if you only check the type field. Reject zero and absurd lengths, and
require at least one keyframe before calling a file playable.
