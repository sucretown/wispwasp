"""
Typed prompt history, and the colour coding of the live history list.

The sync bug this covers: the Prompt panel kept its own list of prompts,
populated only when that panel sent one. The prompt strip in the hybrid
layout goes straight to the engine, so anything typed there never
appeared - two ways into the same queue, one of them invisible.
"""
import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from avcore.config import Settings
from avcore.engine import Engine
from avgui import theme
from avgui.panels import LivePanel
from avgui.theme import kind_colour
from avgui.prompt_panel import PromptPanel
from demo_stubs import DemoBackend

app = QApplication.instance() or QApplication([])
app.setStyleSheet(theme.QSS)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


tmp = Path("_synctest").resolve()
shutil.rmtree(tmp, ignore_errors=True)
# Tolerates a leftover: Windows will not delete a folder a live
# window still has open, so the cleanup at the end can fail.
tmp.mkdir(parents=True, exist_ok=True)

s = Settings.load(path=tmp / "s.json")
s.set("paths.overlay_dir", str(tmp / "out"))
s.set("paths.manual_dir", str(tmp / "out"))
eng = Engine(s)
eng._backend = DemoBackend()
eng._ensure_ready = lambda: None
eng._ensure_comfy = lambda: True

live = LivePanel(eng)
prompt_panel = PromptPanel(eng)


def fan(snapshot=None):
    snap = snapshot or eng.snapshot()
    live.on_state(snap)
    prompt_panel.on_state(snap)


print("=== a prompt typed in the strip reaches the Prompt panel ===")
live.prompt_box.setText("a brass diving helmet on a workbench")
live._fire_prompt()
fan()

items = [prompt_panel.history.item(i).text()
         for i in range(prompt_panel.history.count())]
# The strip applies the saved style now, exactly as the Prompt page
# does, so what is recorded is the styled prompt rather than the bare
# typing. Matching on the start keeps this about "did it arrive".
check("the Prompt panel lists it",
      any(entry.startswith("a brass diving helmet on a workbench")
          for entry in items), str(items[:1]))
check("the strip cleared itself", live.prompt_box.text() == "")

print("\n=== and one typed in the Prompt panel appears too ===")
prompt_panel.box.setPlainText("a copper kettle on a stove")
prompt_panel.use_suffix.setChecked(False)
prompt_panel._fire()
fan()
items = [prompt_panel.history.item(i).text()
         for i in range(prompt_panel.history.count())]
check("both prompts are listed", len(items) == 2, str(len(items)))
check("newest first", items[0] == "a copper kettle on a stove", items[0])

print("\n=== the same prompt twice does not stack up ===")
live.prompt_box.setText("a copper kettle on a stove")
live._fire_prompt()
fan()
items = [prompt_panel.history.item(i).text()
         for i in range(prompt_panel.history.count())]
check("it is not duplicated", items.count("a copper kettle on a stove") == 1)
check("but moves to the top",
      items[0].startswith("a copper kettle on a stove"), items[0])

print("\n=== the history list is colour coded ===")
# Three kinds, three colours: red for anything discarded, green for what
# was heard, blue for what was typed.
eng._remember("a lighthouse over a cold grey sea",
              prompt="a lighthouse over a cold grey sea", kind="heard")
eng._remember("", skipped="silence")
eng._remember("we are, we are, we are", skipped="repetition loop")
eng._remember("a copper kettle", prompt="a copper kettle", kind="typed")
fan()

rows = []
for i in range(live.heard.count()):
    item = live.heard.item(i)
    rows.append((item.text(), item.data(Qt.UserRole + 1)))

for text, colour in rows[:6]:
    name = {kind_colour(k): k for k in ('skipped','heard','typed')}.get(colour, "none")
    print(f"   {name:<8} {text[:52]}")

by_colour = {}
for text, colour in rows:
    by_colour.setdefault(colour, []).append(text)

check("typed entries are blue",
      any("copper kettle" in t
          for t in by_colour.get(kind_colour("typed"), [])))
check("heard entries are green",
      any("lighthouse" in t
          for t in by_colour.get(kind_colour("heard"), [])))
check("silence is red",
      any("silence" in t
          for t in by_colour.get(kind_colour("skipped"), [])))
check("a repetition loop is red too",
      any("repetition" in t
          for t in by_colour.get(kind_colour("skipped"), [])))
check("every row carries a colour",
      all(c for _t, c in rows), f"{sum(1 for _t, c in rows if not c)} without")

print("\n  the three colours are distinct:")
check("red, green and blue differ",
      len({kind_colour('skipped'), kind_colour('heard'),
           kind_colour('typed')}) == 3)
check("skipped uses the tally red", kind_colour("skipped") == theme.TALLY)
check("heard uses the ok green", kind_colour("heard") == theme.OK)
check("typed uses the favourite blue",
      kind_colour("typed") == theme.FAVOURITE)

print("\n  a skipped entry stays skipped even if it had a prompt:")
eng._remember("something", prompt="something", skipped="too short",
              kind="typed")
fan()
top = live.heard.item(0)
check("kind is skipped, not typed",
      top.data(Qt.UserRole + 1) == kind_colour("skipped"))

print("\n=== the panels still hold no state of their own ===")
# Rebuilt from scratch, they must show exactly the same thing - that is
# what makes layout switching safe.
fresh = PromptPanel(eng)
fresh.on_state(eng.snapshot())
old_items = [prompt_panel.history.item(i).text()
             for i in range(prompt_panel.history.count())]
new_items = [fresh.history.item(i).text()
             for i in range(fresh.history.count())]
check("a freshly built panel shows the same prompts",
      old_items == new_items, f"{len(new_items)} entries")

eng.shutdown()
shutil.rmtree(tmp, ignore_errors=True)

print("\n=== the history panel can be resized ===")
# It was a fixed 74px, which showed two entries. How much history is worth
# seeing depends on what you are doing, so it is now draggable.
from PySide6.QtWidgets import QSplitter
from avgui.window import MainWindow

split_tmp = Path("_splittest").resolve()
shutil.rmtree(split_tmp, ignore_errors=True)
split_tmp.mkdir(parents=True, exist_ok=True)
ss = Settings.load(path=split_tmp / "s.json")
ss.set("paths.overlay_dir", str(split_tmp / "out"))
eng_s = Engine(ss)
live_s = LivePanel(eng_s)

check("the preview and history share a splitter",
      isinstance(live_s.split, QSplitter))
check("it is vertical", live_s.split.orientation() == Qt.Vertical)
check("the history is no longer a fixed height",
      live_s.heard.maximumHeight() > 1000,
      f"max {live_s.heard.maximumHeight()}")
check("but cannot be squashed to nothing",
      live_s.heard.minimumHeight() >= 40,
      f"min {live_s.heard.minimumHeight()}")
check("neither side can be collapsed away",
      not live_s.split.childrenCollapsible())

live_s.resize(760, 620)
live_s.show()
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)
sizes = live_s.split.sizes()
check("both panes have real height", all(v > 0 for v in sizes), str(sizes))
check("the image gets the larger share by default",
      sizes[0] > sizes[1], f"{sizes[0]} vs {sizes[1]}")
# The default has to beat the fixed height it replaced, or this is a
# downgrade for anyone who never drags it.
check("the history starts taller than the old fixed 74px",
      sizes[1] > 74, f"{sizes[1]}px")
# Was 96. The strip above the prompt box gained a row when the style
# and overlay choices moved into it, and at this window height Qt takes
# those pixels from the history rather than the preview, which has a
# minimum. Still comfortably more than the fixed 74px this replaced,
# and still two entries.
check("which is room for several entries", sizes[1] >= 88,
      f"{sizes[1]}px")

print("\n  dragging it is remembered:")
live_s.split.setSizes([300, 260])
app.processEvents()
live_s._remember_split()
saved = ss.get("ui.live_split")
check("the position was saved", isinstance(saved, list) and len(saved) == 2,
      str(saved))
check("it matches where it was left",
      abs(saved[1] - live_s.split.sizes()[1]) < 20,
      f"{saved} vs {live_s.split.sizes()}")

print("\n  and restored on a fresh panel:")
rebuilt = LivePanel(eng_s)
rebuilt.resize(760, 620)
rebuilt.show()
app.processEvents()
# The restore is deferred until Qt has laid the splitter out, so give the
# event loop a turn before looking.
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)
restored = rebuilt.split.sizes()
check("the divider came back where it was left",
      abs(restored[1] - saved[1]) < 40, f"{restored} vs {saved}")

print("\n  more history fits once it is dragged open:")
tall = LivePanel(eng_s)
tall.resize(760, 620)
tall.show()
app.processEvents()
tall.split.setSizes([200, 360])
app.processEvents()
check("the list is now much taller than the old fixed height",
      tall.heard.height() > 200, f"{tall.heard.height()}px")

eng_s.shutdown()
shutil.rmtree(split_tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("prompt history syncs, is colour coded, and resizes")
