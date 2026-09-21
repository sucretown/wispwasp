"""
Tests the prompt catalogue and the gallery hover caption.

The point of the catalogue is that it outlives the session, so the
important checks are that it survives being reloaded and that it degrades
quietly when it cannot answer.
"""
import json
import shutil
import time
from pathlib import Path

from avcore.catalog import Catalog

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


tmp = Path("_cattest").resolve()
shutil.rmtree(tmp, ignore_errors=True)
# Tolerates a leftover: Windows will not delete a folder a live
# window still has open, so the cleanup at the end can fail.
tmp.mkdir(parents=True, exist_ok=True)
index = tmp / "prompts.json"

print("=== recording and looking up ===")
cat = Catalog(index)
cat.record(tmp / "live_001.png", "a lighthouse over a cold grey sea",
           source="live", transcript="a lighthouse over a cold grey sea")
entry = cat.lookup(tmp / "live_001.png")
check("a recorded image can be looked up", entry is not None)
check("the prompt comes back", entry
      and "lighthouse" in entry["prompt"])
check("the source is kept", entry and entry["source"] == "live")
check("the time is kept", entry and entry.get("at", 0) > 0)
check("an unknown image returns nothing",
      cat.lookup(tmp / "never_seen.png") is None)

print("\n=== it survives a restart ===")
# The whole reason this exists: a filename is only a timestamp, so without
# persistence the gallery cannot say anything about older images.
again = Catalog(index)
entry = again.lookup(tmp / "live_001.png")
check("a fresh Catalog reads the file back", entry is not None)
check("with the prompt intact", entry
      and entry["prompt"] == "a lighthouse over a cold grey sea")

print("\n=== one prompt, two locations ===")
# A manual image exists where the user asked for it and as a copy in the
# overlay folder; either may be the one the gallery lists.
cat.record([tmp / "desktop" / "ai_1.png", tmp / "overlay" / "ai_1.png"],
           "a brass diving helmet", source="manual")
check("found under the first path",
      cat.lookup(tmp / "desktop" / "ai_1.png") is not None)
check("found under the second path too",
      cat.lookup(tmp / "overlay" / "ai_1.png") is not None)

print("\n=== forgetting ===")
cat.forget(tmp / "live_001.png")
check("a forgotten image is gone", cat.lookup(tmp / "live_001.png") is None)
check("and stays gone after a reload",
      Catalog(index).lookup(tmp / "live_001.png") is None)

print("\n=== a damaged index costs captions, not the app ===")
index.write_text("{ this is not json", encoding="utf-8")
broken = Catalog(index)
check("a corrupt file loads as empty rather than raising",
      len(broken) == 0)
broken.record(tmp / "x.png", "still works")
check("and can be written to again",
      broken.lookup(tmp / "x.png") is not None)

print("\n=== it does not grow without bound ===")
from avcore import catalog as catmod
original = catmod.MAX_ENTRIES
catmod.MAX_ENTRIES = 50
big = Catalog(tmp / "big.json")
for i in range(80):
    big.record(tmp / f"img_{i:03}.png", f"prompt number {i}")
    time.sleep(0.001)
check("capped at the limit", len(big) <= 50, f"{len(big)} entries")
check("the newest are the ones kept",
      big.lookup(tmp / "img_079.png") is not None)
check("the oldest were dropped",
      big.lookup(tmp / "img_000.png") is None)
catmod.MAX_ENTRIES = original

print("\n=== the engine records what it generates ===")
from avcore.config import Settings
from avcore.engine import Engine
from demo_stubs import DemoBackend

s = Settings.load()
s.set("paths.overlay_dir", str(tmp / "out"))
s.set("paths.manual_dir", str(tmp / "out"))
eng = Engine(s)
eng.catalog = Catalog(tmp / "engine.json")
eng._backend = DemoBackend()
eng._ensure_ready = lambda: None
eng.start()
eng.queue_manual("a copper kettle on a stove")

deadline = time.time() + 20
while time.time() < deadline and not eng.snapshot().get("image"):
    time.sleep(0.1)
shown = eng.snapshot().get("image")
check("an image was generated", bool(shown))
entry = eng.catalog.lookup(shown) if shown else None
check("the engine catalogued it", entry is not None)
check("with the prompt that made it",
      entry and "copper kettle" in entry["prompt"], 
      entry["prompt"][:40] if entry else "")
check("tagged as typed rather than heard",
      entry and entry["source"] == "manual")
eng.shutdown()

print("\n=== the hover caption widget ===")
from PySide6.QtCore import QEvent, QPointF, QSize
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication
from avgui import theme
from avgui.widgets import HoverCaption

app = QApplication.instance() or QApplication([])
app.setStyleSheet(theme.QSS)


def enter(widget):
    # Qt type-checks event arguments, so these have to be the real thing.
    pos = QPointF(10, 10)
    widget.enterEvent(QEnterEvent(pos, pos, pos))


def leave(widget):
    widget.leaveEvent(QEvent(QEvent.Leave))


w = HoverCaption(QSize(210, 118))
w.set_caption("a brass orrery turning slowly in a dim observatory",
              "heard   12 Sep, 21:30")
check("it accepts a caption", w._caption.startswith("a brass orrery"))
check("it starts hidden", w._fade == 0.0)
enter(w)
check("hovering starts the reveal", w._hover is True)
leave(w)
check("leaving starts the fade out", w._hover is False)

# An image with no recorded prompt must not pretend to have one.
blank = HoverCaption(QSize(210, 118))
blank.set_caption("")
enter(blank)
check("nothing to say means no reveal", blank._fade == 0.0)

shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("the prompt catalogue works")
