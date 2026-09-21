"""
Sound-activated capture.

The gate is a state machine driven by a level, so it can be tested
directly rather than through a live audio stream - which means the edge
cases that matter get checked rather than hoped for.
"""
import shutil
import time
from pathlib import Path

from avcore.audio import DONE, RECORDING, WAITING, ActivationGate

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


def run(gate, pattern, dt=0.1):
    """Feed a pattern of levels, returning the states seen."""
    seen = []
    for rms in pattern:
        seen.append(gate.feed(rms, dt))
        if gate.state == DONE:
            break
    return seen


print("=== it waits until something is loud enough ===")
g = ActivationGate(threshold=400, silence_seconds=1.0, max_seconds=30,
                   min_seconds=0.5)
run(g, [0, 50, 100, 399] * 3)
check("quiet audio does not start a clip", g.state == WAITING)
check("nothing was recorded", g.recorded == 0)
check("but the wait is tracked", g.waited > 0, f"{g.waited:.1f}s")

g.feed(900, 0.1)
check("a loud chunk starts it", g.state == RECORDING)

print("\n=== it stops once things go quiet for long enough ===")
g = ActivationGate(threshold=400, silence_seconds=1.0, max_seconds=30,
                   min_seconds=0.5)
run(g, [800] * 20 + [10] * 20)
check("it finished", g.state == DONE)
check("because it went quiet", g.reason == "went quiet", g.reason)
check("the trailing silence is not counted as speech",
      abs(g.speech_seconds - 2.0) < 0.25, f"{g.speech_seconds:.1f}s")
check("it is not treated as a blip", not g.too_short())

print("\n=== a brief dip does not end the clip ===")
# Speech has gaps between words; ending on the first quiet chunk would
# cut people off mid-sentence.
g = ActivationGate(threshold=400, silence_seconds=1.0, max_seconds=30,
                   min_seconds=0.5)
run(g, [800] * 10 + [10] * 5 + [800] * 10 + [10] * 15)
check("a half-second gap was ridden through", g.state == DONE)
check("and the whole utterance was kept",
      g.speech_seconds > 2.4, f"{g.speech_seconds:.1f}s")

print("\n=== a noisy room cannot record forever ===")
g = ActivationGate(threshold=400, silence_seconds=2.0, max_seconds=3.0,
                   min_seconds=0.5)
run(g, [900] * 200)
check("the maximum length stopped it", g.state == DONE)
check("and says so", g.reason == "reached the maximum length", g.reason)
check("the clip is the length asked for",
      abs(g.recorded - 3.0) < 0.2, f"{g.recorded:.1f}s")

print("\n=== short blips are discarded ===")
# A door closing or a mouse click clears the threshold for a moment.
g = ActivationGate(threshold=400, silence_seconds=0.5, max_seconds=30,
                   min_seconds=1.5)
run(g, [900] * 3 + [10] * 30)
check("the blip ended the clip", g.state == DONE)
check("and it is recognised as too short", g.too_short(),
      f"{g.speech_seconds:.1f}s of sound")

print("\n=== the engine drives it, and respects the mode ===")
from avcore.config import Settings
from avcore.engine import Engine
from demo_stubs import DemoBackend

tmp = Path("_activationtest").resolve()
shutil.rmtree(tmp, ignore_errors=True)


class ScriptedRecorder:
    """
    A recorder that plays a fixed level pattern through the gate.

    Records which method the engine called, which is how the mode setting
    is verified from the outside rather than by reading it back.
    """
    name = "Scripted"
    label = "Scripted (test)"
    kind = "output"

    def __init__(self, pattern):
        self.pattern = pattern
        self.fixed_calls = 0
        self.activated_calls = 0

    def record(self, path, seconds, cancel=None, on_level=None):
        self.fixed_calls += 1
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake wav")
        return 5000

    def record_activated(self, path, gate, cancel=None, on_level=None,
                         on_state=None):
        self.activated_calls += 1
        for rms in self.pattern:
            if cancel and cancel():
                break
            if on_level:
                on_level(rms)
            state = gate.feed(rms, 0.1)
            if state == DONE:
                break
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake wav")
        return 5000, gate

    def close(self):
        pass


def build(mode, pattern, **extra):
    s = Settings.load()
    s.set("paths.overlay_dir", str(tmp / "out"))
    s.set("audio.mode", mode)
    s.set("audio.activation_rms", 400)
    s.set("audio.activation_silence", 1.0)
    s.set("audio.activation_max", 30)
    s.set("audio.activation_min", 0.5)
    s.set("audio.activation_gap", 0.2)
    s.set("audio.cycle_seconds", 3)
    s.set("audio.record_seconds", 1)
    for k, v in extra.items():
        s.set(k, v)
    eng = Engine(s)
    rec = ScriptedRecorder(pattern)
    eng._recorder = rec
    eng._backend = DemoBackend()
    eng._transcriber = type("T", (), {
        "transcribe": lambda self, p: "a lighthouse over a cold grey sea"})()
    eng._ensure_ready = lambda: None
    eng._ensure_comfy = lambda: True
    return eng, rec


speech = [800] * 20 + [10] * 20

eng, rec = build("cycle", speech)
eng.start()
eng.start_listening()
time.sleep(3)
check("cycle mode uses fixed-length recording", rec.fixed_calls >= 1,
      f"{rec.fixed_calls} call(s)")
check("and never the activated path", rec.activated_calls == 0)
eng.shutdown()

eng, rec = build("activation", speech)
eng.start()
eng.start_listening()
time.sleep(4)
check("activation mode uses the triggered path", rec.activated_calls >= 1,
      f"{rec.activated_calls} call(s)")
check("and never the fixed-length one", rec.fixed_calls == 0)
check("it generated from what it heard",
      eng.snapshot().get("image") is not None)
eng.shutdown()

print("\n  a blip is skipped rather than transcribed:")
eng, rec = build("activation", [900] * 2 + [5] * 40,
                 **{"audio.activation_min": 2.0})
eng.start()
eng.start_listening()
time.sleep(4)
snap = eng.snapshot()
check("it was skipped", "under the" in (snap.get("skip_reason") or ""),
      (snap.get("skip_reason") or "")[:46])
check("nothing was generated from it", snap.get("image") is None)
eng.shutdown()

print("\n  there is no countdown in activation mode:")
eng, rec = build("activation", speech)
eng.start()
eng.start_listening()
time.sleep(2.5)
check("next_in stays empty", eng.snapshot().get("next_in") is None)
eng.shutdown()

shutil.rmtree(tmp, ignore_errors=True)

print("\n=== disabled settings actually look disabled ===")
from avgui import theme

# The stylesheet sets `color` unconditionally on these controls, which
# overrides Qt's own disabled palette - so without explicit :disabled
# rules a greyed-out setting still renders fully lit.
qss = theme.QSS
for selector in ("QLabel:disabled", "QWidget:disabled",
                 "QSpinBox:disabled", "QDoubleSpinBox:disabled",
                 "QComboBox:disabled"):
    check(f"{selector} is styled", selector in qss)


def brightness(hex_colour):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.299 * r + 0.587 * g + 0.114 * b


check("disabled text is dimmer than normal text",
      brightness(theme.DISABLED) < brightness(theme.TEXT) - 60,
      f"{brightness(theme.DISABLED):.0f} vs {brightness(theme.TEXT):.0f}")
check("and clearly dimmer than secondary text too",
      brightness(theme.DISABLED) < brightness(theme.MUTED) - 25,
      f"{brightness(theme.DISABLED):.0f} vs {brightness(theme.MUTED):.0f}")

from PySide6.QtWidgets import QApplication
from avgui.settings_panel import SettingsPanel

_app = QApplication.instance() or QApplication([])
_app.setStyleSheet(theme.QSS)
# A settings file of its own: the panel saves on every change, and writing
# the real one would leave the app - and the next test - in whatever mode
# this test finished in.
_tmpdir = Path("_paneltest").resolve()
_tmpdir.mkdir(exist_ok=True)
_s = Settings.load(path=_tmpdir / "settings.json")
_s.set("audio.mode", "cycle")
panel = SettingsPanel(Engine(_s))

check("cycle fields are live in cycle mode",
      all(f.isEnabled() for _l, f in panel.cycle_rows))
check("and their labels too",
      all(l.isEnabled() for l, _f in panel.cycle_rows))
check("activation fields are off",
      not any(f.isEnabled() for _l, f in panel.activation_rows))
check("and their labels too - not just the boxes",
      not any(l.isEnabled() for l, _f in panel.activation_rows))

panel.mode.setCurrentIndex(panel.mode.findData("activation"))
check("switching flips which group is live",
      all(f.isEnabled() for _l, f in panel.activation_rows)
      and not any(f.isEnabled() for _l, f in panel.cycle_rows))

shutil.rmtree(_tmpdir, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("sound-activated capture works")
