"""
Tests for read_wav, which replaced PyAV's decoding.

Getting the sample maths wrong here would not raise - it would quietly
produce distorted audio and worse transcription, so each format is checked
against a known signal.
"""
import math
import wave
from pathlib import Path

import numpy as np

from avcore.speech import WHISPER_RATE, read_wav

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


tmp = Path("_wavtest").resolve()
tmp.mkdir(exist_ok=True)


def write(path, samples, width, channels, rate):
    """samples: float list in -1..1, interleaved if multi-channel."""
    if width == 2:
        data = (np.array(samples) * 32767).astype(np.int16).tobytes()
    elif width == 4:
        data = (np.array(samples) * 2147483647).astype(np.int32).tobytes()
    elif width == 1:
        data = ((np.array(samples) * 127) + 128).astype(np.uint8).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data)


def tone(n, freq=440, rate=16000, amp=0.5):
    return [amp * math.sin(2 * math.pi * freq * i / rate) for i in range(n)]


print("=== sample widths convert to -1..1 float32 ===")
for width in (1, 2, 4):
    p = tmp / f"w{width}.wav"
    write(p, tone(16000), width, 1, WHISPER_RATE)
    a = read_wav(p)
    peak = float(np.abs(a).max())
    check(f"{width * 8}-bit decodes", a.dtype == np.float32
          and 0.4 < peak < 0.6, f"peak {peak:.3f}")

print("\n=== stereo is mixed down to mono ===")
# Left full scale, right silent. The average must be half scale, not full,
# and the sample count must halve.
frames = 8000
inter = []
for i in range(frames):
    inter.append(0.8)
    inter.append(0.0)
p = tmp / "stereo.wav"
write(p, inter, 2, 2, WHISPER_RATE)
a = read_wav(p)
check("stereo halves the sample count", len(a) == frames, f"{len(a)}")
check("channels are averaged, not concatenated",
      0.35 < float(np.abs(a).max()) < 0.45, f"peak {np.abs(a).max():.3f}")

print("\n=== resampling to 16 kHz ===")
# The loopback device runs at 48 kHz, so this is the path real audio takes.
p = tmp / "48k.wav"
write(p, tone(48000, freq=440, rate=48000), 2, 1, 48000)
a = read_wav(p)
check("48 kHz resamples to 16 kHz", abs(len(a) - 16000) <= 2, f"{len(a)}")

# A sine should survive resampling with its amplitude intact. If the
# interpolation were indexing wrongly the peak would collapse.
check("tone survives resampling", 0.4 < float(np.abs(a).max()) < 0.55,
      f"peak {np.abs(a).max():.3f}")

# Check the frequency is still 440 Hz rather than shifted, which is what a
# rate mistake would cause.
spectrum = np.abs(np.fft.rfft(a * np.hanning(len(a))))
peak_hz = float(np.fft.rfftfreq(len(a), 1 / WHISPER_RATE)[spectrum.argmax()])
check("pitch is unchanged by resampling", abs(peak_hz - 440) < 12,
      f"{peak_hz:.0f} Hz")

print("\n=== already 16 kHz is passed through untouched ===")
p = tmp / "16k.wav"
write(p, tone(16000), 2, 1, WHISPER_RATE)
a = read_wav(p)
check("no resampling applied", len(a) == 16000, f"{len(a)}")

print("\n=== stereo 48 kHz, the real capture format ===")
inter = []
src = tone(48000, freq=440, rate=48000)
for s in src:
    inter.append(s)
    inter.append(s)
p = tmp / "real.wav"
write(p, inter, 2, 2, 48000)
a = read_wav(p)
check("stereo 48k becomes mono 16k", abs(len(a) - 16000) <= 2, f"{len(a)}")
check("contiguous, as ctranslate2 needs", a.flags["C_CONTIGUOUS"])

print("\n=== an unsupported width fails loudly ===")
p = tmp / "odd.wav"
with wave.open(str(p), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(3)          # 24-bit
    w.setframerate(WHISPER_RATE)
    w.writeframes(b"\x00" * 300)
try:
    read_wav(p)
    check("24-bit raises rather than returning garbage", False)
except ValueError as exc:
    check("24-bit raises rather than returning garbage", True, str(exc))

import shutil
shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("wav reading is correct")
