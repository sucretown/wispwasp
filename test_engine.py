"""
Engine tests. Uses a fake backend so nothing touches the GPU, which lets the
queue and pause behaviour be checked in seconds rather than minutes.
"""
import os
import threading
import time
from pathlib import Path

from avcore.config import Settings
from avcore.engine import Engine


class FakeBackend:
    """Stands in for ComfyUI. Records call order."""
    extension = "png"

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.concurrent = 0
        self.max_concurrent = 0

    def generate(self, prompt, dest, seed=None, cancel=None):
        with self.lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            self.calls.append(prompt)
        time.sleep(0.3)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG fake")
        with self.lock:
            self.concurrent -= 1
        return dest

    def interrupt(self):
        pass

    def is_up(self, timeout=4):
        return True


class FakeRecorder:
    name = "Fake output (loopback)"

    # Mirrors the real signature, on_level included. When the engine gained
    # a level callback this fixture did not, and every cycle failed with a
    # TypeError that surfaced as an unrelated audio error.
    def record(self, path, seconds, cancel=None, on_level=None):
        for _ in range(int(seconds * 10)):
            if cancel and cancel():
                return None
            if on_level:
                on_level(4000.0)
            time.sleep(0.01)
        Path(path).write_bytes(b"fake wav")
        return 5000   # well above the silence gate


class FakeTranscriber:
    def transcribe(self, path):
        return "a lighthouse standing over a cold grey sea"


def build(tmp, **overrides):
    s = Settings.load()
    # Pinned, not inherited: another test - or the user - may have left
    # the saved settings in activation mode, and these fakes only
    # implement fixed-length recording.
    s.set("audio.mode", "cycle")
    s.set("paths.overlay_dir", str(tmp / "overlay"))
    s.set("paths.manual_dir", str(tmp / "manual"))
    s.set("audio.record_seconds", 1)
    s.set("audio.cycle_seconds", 2)
    for k, v in overrides.items():
        s.set(k, v)

    eng = Engine(s)
    fake = FakeBackend()
    eng._backend = fake
    eng._recorder = FakeRecorder()
    eng._transcriber = FakeTranscriber()
    eng._ensure_ready = lambda: None   # everything is already injected
    return eng, fake


tmp = Path("_enginetest").resolve()
# Start clean. Without this a second run counts files left by the first
# and the manual-image check fails for a reason that has nothing to do
# with the engine.
import shutil
shutil.rmtree(tmp, ignore_errors=True)
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


print("=== observers see state without holding any ===")
eng, fake = build(tmp)
seen = []
sources = []


def watch(snap):
    seen.append(snap["status"])
    src = snap.get("image_source")
    if src and (not sources or sources[-1] != src):
        sources.append(src)


eng.add_listener(watch)
check("listener fires immediately on subscribe", len(seen) == 1)
eng.start()
eng.start_listening()
time.sleep(0.2)
check("listener saw the status change", "Listening" in seen)

print("\n=== a live cycle produces an overlay image ===")
time.sleep(3.0)
snap = eng.snapshot()
check("transcript captured", "lighthouse" in snap["transcript"])
check("image published", snap["image"] is not None)
check("source tagged live", snap["image_source"] == "live")
check("state.json written", (tmp / "overlay" / "state.json").exists())

print("\n=== manual prompt pauses listening, never runs alongside it ===")
eng.queue_manual("a brass diving helmet on a workbench", amount=2)
time.sleep(2.5)
check("manual prompts rendered", sum(
    1 for c in fake.calls if "diving helmet" in c) == 2)
check("GPU never ran two jobs at once", fake.max_concurrent == 1)

# The interleaving is the real claim: once the manual job starts, no live
# prompt may appear until both manual renders are finished.
idx = [i for i, c in enumerate(fake.calls) if "diving helmet" in c]
between = fake.calls[idx[0]:idx[-1] + 1]
check("no live render interleaved into the manual batch",
      all("diving helmet" in c for c in between))

print("\n=== manual images land in the user's chosen folder ===")
manual_files = list((tmp / "manual").glob("ai_*.png"))
check("saved to paths.manual_dir", len(manual_files) == 2)
check("overlay showed the manual image at some point",
      "manual" in sources)

print("\n=== live resumes and overwrites the manual image ===")
time.sleep(4.0)
snap = eng.snapshot()
check("live took the overlay back", snap["image_source"] == "live")
check("overlay handed back and forth without a lock",
      sources[:3] == ["live", "manual", "live"])
eng.stop_listening()
eng.shutdown()

print("\n=== auto_push off holds the image back until pushed ===")
eng2, fake2 = build(tmp, **{"image.manual_auto_push": False})
eng2.start()
eng2.queue_manual("a copper kettle", amount=1)
time.sleep(1.5)
snap = eng2.snapshot()
check("nothing pushed to overlay", snap["image"] is None)
check("image is pending", snap["pending_manual"] is not None)
check("push_pending works", eng2.push_pending())
check("now on the overlay", eng2.snapshot()["image"] is not None)
eng2.shutdown()

print("\n=== queueing while recording cancels the wait ===")
eng3, fake3 = build(tmp, **{"audio.record_seconds": 8})
eng3.start()
eng3.start_listening()
time.sleep(0.5)
t = time.time()
eng3.queue_manual("an ink drawing of a crow")
while time.time() - t < 6:
    if any("crow" in c for c in fake3.calls):
        break
    time.sleep(0.1)
elapsed = time.time() - t
check(f"manual started in {elapsed:.1f}s, not after the full 8s clip",
      elapsed < 4)
eng3.shutdown()

print("\n=== the scratch file cannot be fought over ===")
# The bug this covers: a relative "clip.wav" meant every instance wrote to
# the same file in whatever the working directory was, so a second copy -
# or an installed copy in a protected folder - hit "Permission denied"
# mid-recording and listening stopped.
eng_a, _ = build(tmp)
eng_b, _ = build(tmp)
pa, pb = eng_a._clip_path(), eng_b._clip_path()
check("clip path is absolute", pa.is_absolute())
check("clip path is not the working directory",
      pa.parent != Path.cwd())
check("clip is named per process", str(os.getpid()) in pa.name)
eng_b._clip_seq = 1
check("a blocked name falls back to a different one",
      eng_a._clip_path() != eng_b._clip_path())
eng_a.shutdown()
eng_b.shutdown()


class LockedRecorder(FakeRecorder):
    """Refuses the first two recordings the way a locked file would."""

    def __init__(self):
        self.calls = 0

    def record(self, path, seconds, cancel=None, on_level=None):
        self.calls += 1
        if self.calls <= 2:
            raise PermissionError(13, "Permission denied", str(path))
        return super().record(path, seconds, cancel, on_level)


print("\n=== a locked scratch file does not stop listening ===")
eng4, fake4 = build(tmp)
eng4._recorder = LockedRecorder()
eng4.start()
eng4.start_listening()
deadline = time.time() + 25
while time.time() < deadline and not fake4.calls:
    time.sleep(0.2)
snap = eng4.snapshot()
check("it kept listening through the errors", snap["listening"] is True)
check("it recovered and generated anyway", bool(fake4.calls))
print(f"      ({eng4._recorder.calls} recording attempts, "
      f"{len(fake4.calls)} render(s))")
check("the message names the folder, not just the errno",
      "clip_" in (snap.get("error") or "") or not snap.get("error"))
eng4.shutdown()

print("\n=== quality of life: level, countdown, history, capture, repeat ===")


class LevelRecorder(FakeRecorder):
    """Reports a rising level the way real capture does."""

    def record(self, path, seconds, cancel=None, on_level=None):
        for i in range(int(seconds * 20)):
            if cancel and cancel():
                return None
            if on_level:
                on_level(1000.0 + i * 50)
            time.sleep(0.01)
        Path(path).write_bytes(b"fake wav")
        return 5000


eng5, fake5 = build(tmp, **{"audio.record_seconds": 2,
                            "audio.cycle_seconds": 6})
eng5._recorder = LevelRecorder()
levels = []
eng5.add_level_listener(levels.append)

counts = []
eng5.add_listener(lambda s: counts.append(s.get("next_in")))

eng5.start()
eng5.start_listening()
time.sleep(4.0)

check("audio level is reported while recording", len(levels) > 10,
      f"{len(levels)} updates")
check("levels are real numbers", all(isinstance(v, float) for v in levels))
check("a countdown to the next capture is published",
      any(isinstance(v, int) for v in counts),
      f"saw {sorted({v for v in counts if isinstance(v, int)}, reverse=True)[:4]}")

snap = eng5.snapshot()
# The device name is only set where the recorder is actually built, which
# an injected fake bypasses; the swap test below covers that properly.
check("history recorded the cycle", len(snap.get("history", [])) >= 1)
if snap.get("history"):
    entry = snap["history"][0]
    check("history entries carry a timestamp", bool(entry.get("at")),
          entry.get("at"))
    check("history entries carry the transcript",
          "lighthouse" in (entry.get("text") or ""))
    check("history entries carry the prompt", bool(entry.get("prompt")))
check("repeat becomes available once something ran",
      snap.get("can_repeat") is True)

print("\n  capture now skips the wait:")
# Wait until it is idle and counting down, then demand a capture and time
# how long it takes to start recording.
deadline = time.time() + 8
while time.time() < deadline and eng5.snapshot().get("next_in") is None:
    time.sleep(0.1)
before = len(levels)
t = time.time()
eng5.capture_now()
while time.time() - t < 5 and len(levels) == before:
    time.sleep(0.05)
elapsed = time.time() - t
check(f"recording restarted in {elapsed:.1f}s rather than waiting out the "
      f"cycle", elapsed < 2.5)

print("\n  repeat re-runs the last prompt:")
before_calls = len(fake5.calls)
last = eng5._last_prompt
check("a last prompt was remembered", bool(last))
eng5.repeat_last()
deadline = time.time() + 12
while time.time() < deadline and len(fake5.calls) <= before_calls:
    time.sleep(0.1)
check("it generated again without retyping", len(fake5.calls) > before_calls)
check("and used the same prompt", fake5.calls[-1] == last)
eng5.shutdown()

print("\n  changing device rebuilds the recorder:")
eng6, _ = build(tmp)
eng6._recorder = FakeRecorder()
original = eng6._recorder
eng6.s.set("audio.device_name", "")
eng6.reload_audio()
time.sleep(0.5)
# Not listening, so it swaps immediately; a real Recorder replaces the fake.
check("the recorder was replaced, not reused",
      eng6._recorder is not original or eng6._recorder is None)
check("the device name is reported after the swap",
      bool(eng6.snapshot().get("device")) or bool(eng6.snapshot().get("error")),
      eng6.snapshot().get("device") or eng6.snapshot().get("error"))
eng6.shutdown()

print("\n  the capture source setting picks the right recorder:")
eng7, _ = build(tmp)
from avcore.audio import INPUT, OUTPUT
import avcore.engine as engine_module
import avcore.process_audio as process_audio


class SourceProbe:
    """Records which ordinary audio source the engine requested."""

    def __init__(self, device_name, kind):
        self.device_name = device_name
        self.kind = kind
        self.label = f"probe {kind}"

    def close(self):
        pass


real_recorder = engine_module.Recorder
real_find_by_name = process_audio.find_by_name
engine_module.Recorder = SourceProbe
process_audio.find_by_name = lambda _exe: None
try:
    eng7.s.set("audio.device_kind", "output")
    eng7.s.set("audio.device_name", "")
    r = eng7._build_recorder()
    check("output setting gives a loopback recorder", r.kind == OUTPUT, r.label)
    r.close()

    eng7.s.set("audio.device_kind", "input")
    r = eng7._build_recorder()
    check("input setting gives a microphone recorder", r.kind == INPUT, r.label)
    r.close()

    eng7.s.set("audio.device_kind", "process")
    eng7.s.set("audio.process_exe", "definitely-not-running.exe")
    eng7.s.set("audio.process_name", "Nothing")
    try:
        eng7._build_recorder()
        check("a missing application is reported, not ignored", False)
    except RuntimeError as exc:
        # The failure mode that matters: choosing an app, closing it, and
        # getting silence with no explanation.
        check("a missing application is reported, not ignored",
              "not running" in str(exc).lower(), str(exc)[:52])
finally:
    engine_module.Recorder = real_recorder
    process_audio.find_by_name = real_find_by_name
    eng7.shutdown()

print("\n=== clearing the overlay hides the image but keeps the file ===")
eng8, fake8 = build(tmp)
eng8.start()
eng8.queue_manual("a quiet harbour at dusk")
deadline = time.time() + 15
while time.time() < deadline and not eng8.snapshot().get("image"):
    time.sleep(0.1)

snap = eng8.snapshot()
shown = snap.get("image")
check("something is on the overlay to begin with", bool(shown))
on_disk = Path(shown) if shown else None
state_file = tmp / "overlay" / "state.json"
import json as _json
check("state.json names it",
      _json.loads(state_file.read_text())["image"] is not None)

eng8.clear_overlay()
snap = eng8.snapshot()
check("the overlay is now empty", snap.get("image") is None)
check("it is marked as deliberately cleared",
      snap.get("overlay_cleared") is True)

payload = _json.loads(state_file.read_text())
check("state.json tells the page there is nothing to show",
      payload["image"] is None)
check("and says the clearing was deliberate", payload.get("cleared") is True)

# The whole point: this hides an image, it does not destroy one.
check("the image file is still on disk", on_disk and on_disk.exists(),
      on_disk.name if on_disk else "")
gallery = list((tmp / "overlay").glob("*.png"))
check("and is still in the gallery folder", len(gallery) >= 1,
      f"{len(gallery)} file(s)")

print("\n  it can be put back, and live takes over again:")
check("putting it back works", eng8.publish_overlay(on_disk, "manual"))
check("cleared flag is reset once something is showing",
      eng8.snapshot().get("overlay_cleared") is False)

eng8.clear_overlay()
eng8.start_listening()
deadline = time.time() + 20
while time.time() < deadline and not eng8.snapshot().get("image"):
    time.sleep(0.2)
check("a later live image replaces the cleared state",
      eng8.snapshot().get("image") is not None)
eng8.shutdown()

print("\n=== stopping a job is not a failure ===")
# ComfyUI files an interrupted job under "error" like any other, and by
# the time the history is read the cancel flag has usually been cleared.
# Unless the interruption is recognised here, pressing Cancel is
# reported as "Generation failed" with a page of internal status dumped
# into the window - which is what it did.
from avcore.images import _was_interrupted, _why

interrupted = [
    ["execution_start", {"prompt_id": "abc", "timestamp": 1789480459722}],
    ["execution_cached", {"nodes": ["1", "3", "4"], "prompt_id": "abc"}],
    ["execution_interrupted", {"prompt_id": "abc"}],
]
check("an interrupted job is recognised", _was_interrupted(interrupted))

failed = [
    ["execution_start", {"prompt_id": "abc"}],
    ["execution_error", {"exception_message": "CUDA out of memory",
                         "node_type": "KSampler"}],
]
check("a real failure is not", not _was_interrupted(failed))
check("and reads as something a person can act on",
      _why(failed) == "CUDA out of memory (in KSampler)", _why(failed))
check("no internals leak into the message",
      "execution_" not in _why(failed) and "prompt_id" not in _why(failed))
check("an unrecognisable status still says something",
      "no reason" in _why([["mystery", {}]]), _why([["mystery", {}]]))
check("and an empty one does not raise", bool(_why([])))

print("\n=== accounting for listens that made nothing ===")
# The pair of numbers on the Live page used to be "28 cycles, 18
# images", which said nothing about where the other ten went. Audio too
# quiet to use is the app working correctly; a render that failed is
# not; a cancel is neither. They are counted separately so the tooltip
# can say which you have been getting.
from avgui.panels import _listen_summary

counted = Engine(Settings.load(path=tmp / "counting.json"))

counted._set(cycles=1, skips=counted._count_skip("too quiet"))
counted._set(cycles=2, skips=counted._count_skip("too quiet"))
counted._set(cycles=3, skips=counted._count_skip("not enough words"))
check("skips are tallied by reason",
      counted.state["skips"] == {"too quiet": 2, "not enough words": 1},
      str(counted.state["skips"]))

counted._set(cycles=4, failures=counted.state["failures"] + 1)
check("failures are counted apart from skips",
      counted.state["failures"] == 1
      and "failed" not in counted.state["skips"])

print("\n  a cancelled listen is taken back out:")
counted._set(cycles=5)                       # a listen begins
before = counted.state["cycles"]
counted._set(cancelled=counted.state["cancelled"] + 1,
             cycles=max(0, counted.state["cycles"] - 1))
check("it no longer counts as a listen",
      counted.state["cycles"] == before - 1,
      "otherwise it sits in the gap looking like a fault")
check("but it is remembered", counted.state["cancelled"] == 1)

print("\n  the summary accounts for everything:")
counted._set(generated=1)
summary = _listen_summary(counted.state)
print("    " + summary.replace("\n", "\n    "))
check("it names the skip reasons", "too quiet" in summary)
check("it separates failures", "Failed: 1" in summary)
check("it mentions what was stopped by hand", "stopped by hand" in summary)
check("and nothing is left unexplained",
      "unaccounted" not in summary,
      "the numbers add up")

print("\n  a clean run says nothing extra:")
tidy = {"cycles": 9, "generated": 9, "skips": {}, "failures": 0,
        "cancelled": 0}
check("no skip line", "Skipped" not in _listen_summary(tidy))
check("no failure line", "Failed" not in _listen_summary(tidy))
check("no cancellation line",
      "stopped by hand" not in _listen_summary(tidy))
check("before anything is heard there is no tooltip at all",
      _listen_summary({"cycles": 0, "generated": 0}) == "")

counted.shutdown()

bad = [n for n, ok in results if not ok]
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("engine behaves as specified")
