"""
Checks that capture works from a microphone as well as from playback.

Loopback and microphone devices can share a name - "Razer Kraken V4 X" is
both an output and a mic - so this also checks the kind is respected and
not guessed from the name.
"""
import time
from pathlib import Path

from avcore.audio import INPUT, OUTPUT, Recorder, list_devices
from avcore.speech import read_wav

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


print("=== enumeration ===")
everything = list_devices()
outputs = list_devices(OUTPUT)
inputs = list_devices(INPUT)
check("finds playback devices", len(outputs) > 0, f"{len(outputs)}")
check("finds microphones", len(inputs) > 0, f"{len(inputs)}")
check("both lists together are the full list",
      len(everything) == len(outputs) + len(inputs))
check("every device is tagged with a kind",
      all(d["kind"] in (OUTPUT, INPUT) for d in everything))
check("playback devices come first",
      [d["kind"] for d in everything][:len(outputs)] == [OUTPUT] * len(outputs))

print("\n  microphones found:")
for d in inputs:
    print(f"     {d['name'][:52]}",
          "(default)" if d["is_default"] else "")

print("\n=== a shared name resolves by kind, not by name ===")
shared = None
for i in inputs:
    for o in outputs:
        key = i["name"].replace("Microphone (", "").rstrip(")")
        if key and key in o["name"]:
            shared = (key, i, o)
            break
    if shared:
        break

if shared:
    key, mic, out = shared
    print(f"  '{key}' exists as both an output and a microphone")
    r_in = Recorder(key, INPUT)
    r_out = Recorder(key, OUTPUT)
    check("asking for the microphone gives a microphone",
          r_in.device["index"] == mic["index"], r_in.name)
    check("asking for the output gives the loopback",
          r_out.device["index"] == out["index"], r_out.name)
    check("they are different devices",
          r_in.device["index"] != r_out.device["index"])
    r_in.close()
    r_out.close()
else:
    print("  no name is shared between the two lists; skipping")

print("\n=== recording from a microphone ===")
tmp = Path("_mictest").resolve()
tmp.mkdir(exist_ok=True)
clip = tmp / "mic.wav"

rec = Recorder("", INPUT)
print(f"  using: {rec.label}")
levels = []
t = time.time()
rms = rec.record(clip, 3, on_level=levels.append)
took = time.time() - t
rec.close()

check("it recorded without error", clip.exists())
check("it took about the right time", 2.5 < took < 4.5, f"{took:.1f}s")
check("levels were reported", len(levels) > 10, f"{len(levels)} updates")
check("the file is readable as audio", True)
audio = read_wav(clip)
check("decodes to 16 kHz mono float", audio.dtype.name == "float32"
      and len(audio) > 16000 * 2, f"{len(audio)} samples")
print(f"  RMS: {rms:.0f}  (a silent mic reads near zero, which is normal "
      f"if nothing is speaking)")

import shutil
shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("microphone capture works")
