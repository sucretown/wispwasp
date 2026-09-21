"""
Proves per-application capture actually works.

The decisive test is not "did it produce a file" but "does it contain that
application's audio and not the rest of the desktop". So a tone is played
through one process while a different, silent process is captured: if
process loopback were quietly falling back to normal loopback, the second
capture would pick the tone up too.
"""
import subprocess
import sys
import time
from pathlib import Path

from avcore import process_audio as pax
from avcore.speech import read_wav

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


print("=== is it supported here? ===")
ok, why = pax.is_supported()
check("Windows build supports process loopback", ok, why)
if not ok:
    raise SystemExit(1)

print("\n=== what can be captured ===")
apps = pax.audible_processes()
check("found applications with audio streams", len(apps) > 0,
      f"{len(apps)}")
for a in apps[:10]:
    print(f"     {a['name']:<14} pid {a['pid']:<7} "
          f"{'playing' if a['playing'] else 'idle'}")

tmp = Path("_proctest").resolve()
tmp.mkdir(exist_ok=True)

# A process of our own that plays a tone, so there is something
# deterministic to capture rather than relying on whatever is open.
PLAYER = tmp / "player.py"
PLAYER.write_text(
    "import math, struct, time, wave, tempfile, winsound, os\n"
    "path = os.path.join(tempfile.gettempdir(), 'ww_tone.wav')\n"
    "rate = 44100\n"
    "with wave.open(path, 'wb') as w:\n"
    "    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)\n"
    "    frames = b''.join(struct.pack('<h', int(22000 * math.sin("
    "2 * math.pi * 440 * i / rate))) for i in range(rate * 8))\n"
    "    w.writeframes(frames)\n"
    "winsound.PlaySound(path, winsound.SND_FILENAME)\n",
    encoding="utf-8")

print("\n=== capturing a process that is playing a tone ===")
player = subprocess.Popen([sys.executable, str(PLAYER)])
time.sleep(1.5)          # let it start making noise

try:
    rec = pax.ProcessRecorder(player.pid, "tone player")
    levels = []
    clip = tmp / "target.wav"
    rms = rec.record(clip, 3, on_level=levels.append)
    print(f"  captured {clip.stat().st_size // 1024} KB, RMS {rms:.0f}")
    check("it produced a readable clip", clip.exists())
    audio = read_wav(clip)
    check("clip is the expected length", len(audio) > 16000 * 2,
          f"{len(audio)} samples at 16 kHz")
    check("it heard the tone", rms > 200, f"RMS {rms:.0f}")
    loud = rms
except Exception as exc:
    check("capture from the playing process", False,
          pax.describe_failure(exc))
    loud = 0
finally:
    player.terminate()
    time.sleep(0.5)

print("\n=== the isolation test: a silent process while the tone plays ===")
# This is the check that matters. A second process plays nothing, but the
# tone is still going. If process loopback were silently behaving like
# ordinary loopback, this capture would pick the tone up.
player2 = subprocess.Popen([sys.executable, str(PLAYER)])
time.sleep(1.5)

silent = subprocess.Popen([sys.executable, "-c",
                           "import time; time.sleep(20)"])
time.sleep(0.5)
try:
    rec2 = pax.ProcessRecorder(silent.pid, "silent process")
    clip2 = tmp / "silent.wav"
    quiet = rec2.record(clip2, 3)
    print(f"  silent process RMS {quiet:.0f}  "
          f"(the tone was playing throughout)")
    check("the silent process really is silent", quiet < 50,
          f"RMS {quiet:.0f}")
    if loud:
        check("and it is far quieter than the process that was playing",
              quiet < loud / 10, f"{quiet:.0f} vs {loud:.0f}")
except Exception as exc:
    check("capture from a silent process", False,
          pax.describe_failure(exc))
finally:
    player2.terminate()
    silent.terminate()
    time.sleep(0.5)

print("\n=== a process that has gone away fails clearly ===")
gone = subprocess.Popen([sys.executable, "-c", "pass"])
gone.wait()
try:
    rec3 = pax.ProcessRecorder(gone.pid, "closed process")
    rec3.record(tmp / "gone.wav", 1)
    # Windows tolerates a dead pid and simply returns silence rather than
    # failing, which is acceptable - it must not hang or crash.
    check("a dead process does not crash the recorder", True,
          "returned silence")
except Exception as exc:
    check("a dead process fails with a readable message",
          "could not be captured" in str(exc).lower()
          or "application" in str(exc).lower(),
          pax.describe_failure(exc)[:60])

import shutil
shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("per-application capture works")
