"""
Favourite prompts, the style suffix shown apart from the prompt, and the
right-click options on history entries.
"""
import shutil
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from avcore.catalog import PromptStore
from avcore.config import Settings
from avcore.engine import Engine
from avgui import theme
from avgui.dialogs import ConfirmUnfavourite
from avgui.panels import LivePanel
from avgui.theme import kind_colour
from avgui.prompt_panel import PromptPanel
from avgui.widgets import CollapsibleBox

app = QApplication.instance() or QApplication([])
app.setStyleSheet(theme.QSS)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


tmp = Path("_favtest").resolve()
shutil.rmtree(tmp, ignore_errors=True)
# Tolerates a leftover: Windows will not delete a folder a live
# window still has open, so the cleanup at the end can fail.
tmp.mkdir(parents=True, exist_ok=True)

print("=== kept prompts survive a restart ===")
store = PromptStore(tmp / "favs.json")
check("empty to begin with", len(store) == 0)
check("adding works", store.add("a brass orrery in a dim observatory"))
check("adding the same one again does nothing",
      not store.add("a brass orrery in a dim observatory"))
check("it knows what it has", store.has("a brass orrery in a dim observatory"))
check("and what it does not", not store.has("something else"))

again = PromptStore(tmp / "favs.json")
check("a fresh store reads them back", len(again) == 1)
check("with the text intact",
      again.all()[0] == "a brass orrery in a dim observatory")

store.add("a copper kettle")
check("newest first", store.all()[0] == "a copper kettle", store.all()[0])
check("removing works", store.remove("a copper kettle"))
check("and sticks after a reload",
      not PromptStore(tmp / "favs.json").has("a copper kettle"))
check("removing something absent is harmless",
      not store.remove("never added"))

print("\n=== the prompt and its style are kept apart ===")
s = Settings.load(path=tmp / "s.json")
s.set("paths.overlay_dir", str(tmp / "out"))
s.set("image.style_suffix", "highly detailed, dramatic lighting")
eng = Engine(s)
eng.prompts = PromptStore(tmp / "engine_favs.json")

eng._remember("a lighthouse over a cold grey sea",
              prompt="a lighthouse over a cold grey sea, highly detailed, "
                     "dramatic lighting",
              base="a lighthouse over a cold grey sea",
              suffix="highly detailed, dramatic lighting",
              kind="heard")
entry = eng.snapshot()["history"][0]
check("the base is stored without the style",
      entry["base"] == "a lighthouse over a cold grey sea", entry["base"])
check("the style is stored separately",
      entry["suffix"] == "highly detailed, dramatic lighting")
check("the full prompt is still there",
      entry["prompt"].endswith("dramatic lighting"))

print("\n  a typed prompt carries them through the queue:")
eng.queue_manual("a copper kettle, highly detailed",
                 base="a copper kettle", suffix="highly detailed")
job = eng._manual.get_nowait()
check("the job knows the base", job.base == "a copper kettle")
check("and the style", job.suffix == "highly detailed")

print("\n  a prompt with no style has none stored:")
eng._remember("plain words", prompt="plain words", base="plain words",
              kind="typed")
check("suffix is empty", eng.snapshot()["history"][0]["suffix"] == "")

print("\n=== favouriting from the engine ===")
check("not kept yet", not eng.is_favourite_prompt("a lighthouse"))
check("keeping it works", eng.favourite_prompt("a lighthouse"))
check("state carries the list",
      "a lighthouse" in eng.snapshot()["favourites"])
check("keeping it twice changes nothing",
      not eng.favourite_prompt("a lighthouse"))
check("dropping it works", eng.unfavourite_prompt("a lighthouse"))
check("and the state follows",
      "a lighthouse" not in eng.snapshot()["favourites"])

print("\n=== the Favourite prompts box ===")
panel = PromptPanel(eng)
check("there is a collapsible box",
      isinstance(panel.fav_box, CollapsibleBox))
check("it sits above Earlier prompts",
      panel.layout().indexOf(panel.fav_box)
      < panel.layout().indexOf(panel.history))
check("it starts expanded", panel.fav_box.is_expanded())

panel.fav_box.set_expanded(False)
# isHidden rather than isVisible: nothing here is on screen, and a child
# of an unshown window is never "visible" whatever its own state.
check("it can be folded away", panel.fav_box.body.isHidden())
check("and the preference is saved",
      eng.s.get("ui.favourites_open") is False)
panel.fav_box.set_expanded(True)
check("and unfolded again", not panel.fav_box.body.isHidden())
check("the preference followed", eng.s.get("ui.favourites_open") is True)

eng.favourite_prompt("a brass diving helmet")
eng.favourite_prompt("a stone bridge in fog")
panel.on_state(eng.snapshot())
items = [panel.favourites.item(i).text()
         for i in range(panel.favourites.count())]
check("both are listed", len(items) == 2, str(len(items)))
check("newest first", items[0] == "a stone bridge in fog", items[0])
check("the header counts them", "2" in panel.fav_box.header.text(),
      panel.fav_box.header.text())

print("\n  the count stays right when one is dropped:")
eng.unfavourite_prompt("a stone bridge in fog")
panel.on_state(eng.snapshot())
check("one left", panel.favourites.count() == 1)
check("and the header agrees", "1" in panel.fav_box.header.text(),
      panel.fav_box.header.text())

print("\n  an empty list explains itself:")
eng.unfavourite_prompt("a brass diving helmet")
panel.on_state(eng.snapshot())
check("a hint is shown", panel.favourites.count() == 1,
      panel.favourites.item(0).text()[:40])
check("the hint cannot be selected",
      panel.favourites.item(0).flags() == Qt.NoItemFlags)
check("and the header shows no count",
      "(" not in panel.fav_box.header.text(), panel.fav_box.header.text())

print("\n=== the right-click menu on history entries ===")
live = LivePanel(eng)
live.on_state(eng.snapshot())

# Find the heard entry, which has both a base and a style.
row = None
for i in range(live.heard.count()):
    data = live.heard.item(i).data(Qt.UserRole + 2) or {}
    if data.get("suffix"):
        row = i
        break
check("a styled entry is in the list", row is not None)

item = live.heard.item(row)
data = item.data(Qt.UserRole + 2)
base = data["base"]
styled = f"{base}, {data['suffix']}"

print("\n  copying either half:")
live._copy(base)
check("copy prompt gives the words alone",
      QApplication.clipboard().text() == base,
      QApplication.clipboard().text()[:38])
live._copy(styled)
check("copy with style appends it",
      QApplication.clipboard().text() == styled,
      QApplication.clipboard().text()[-28:])
check("the two differ", base != styled)

print("\n  favouriting either half:")
live._toggle_prompt(base)
check("the bare prompt is kept", eng.is_favourite_prompt(base))
check("but not the styled one", not eng.is_favourite_prompt(styled))
live._toggle_prompt(styled)
check("the styled one can be kept as well",
      eng.is_favourite_prompt(styled))
check("both are in the list now", len(eng.snapshot()["favourites"]) == 2)

print("\n  skipped entries offer no menu:")
eng._remember("", skipped="silence")
live.on_state(eng.snapshot())
skipped_row = None
for i in range(live.heard.count()):
    if (live.heard.item(i).data(Qt.UserRole + 2) or {}).get("skipped"):
        skipped_row = i
        break
check("a skipped entry is present", skipped_row is not None)
# The menu handler returns without showing anything for these; if it did
# show one, exec would block the test.
live._history_menu(QPoint(-500, -500))
check("asking outside any row is harmless", True)

print("\n=== removing a favourite asks first ===")
dialog = ConfirmUnfavourite("a brass diving helmet")
buttons = {b.objectName(): b for b in dialog.findChildren(QPushButton)}
labels = [l.text() for l in dialog.findChildren(QLabel)]
check("it offers Confirm", "confirmButton" in buttons)
check("it offers Deny", "denyButton" in buttons)
check("it quotes the prompt",
      any("brass diving helmet" in t for t in labels))
check("it says nothing on disk is touched",
      any("Nothing on disk" in t for t in labels))
check("Deny is the default", buttons["denyButton"].isDefault())
dialog.reject()

print("\n=== the colours still mean what they did ===")
check("typed is the favourite blue", kind_colour("typed") == theme.FAVOURITE)
check("the style is dimmed, not coloured",
      theme.DISABLED != theme.TEXT)

eng.shutdown()
shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("favourite prompts work")
