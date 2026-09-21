"""
Holding settings changes until they are applied.

The important guarantee is negative: nothing the user has not confirmed
reaches disk, so closing the app - or crashing - loses only what was never
applied.
"""
import json
import shutil
import time
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QLabel, QPushButton,
)

from avcore.config import DEFAULTS, Settings
from avcore.engine import Engine
from avcore.images import ComfyBackend

# The checkpoint picker is filled from a running ComfyUI. Without one it
# holds a single placeholder, so the field audit below has nothing to
# change it to and reports it as broken - a false alarm that depends
# entirely on whether ComfyUI happens to be running on this machine.
# Two names, always, so the audit can drive it like any other field.
ComfyBackend.list_checkpoints = lambda self: ["alpha.safetensors",
                                              "beta.safetensors"]
from avgui import theme
from avgui.overlays import ChangeBar, Vignette
from avgui.settings_panel import SettingsPanel
from avgui.window import MainWindow

app = QApplication.instance() or QApplication([])
app.setStyleSheet(theme.QSS)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


def pump(seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


tmp = Path("_confirmtest").resolve()
shutil.rmtree(tmp, ignore_errors=True)
# Tolerates a leftover: Windows will not delete a folder a live
# window still has open, so the cleanup at the end can fail.
tmp.mkdir(parents=True, exist_ok=True)
cfg = tmp / "settings.json"

s = Settings.load(path=cfg)
s.set("paths.overlay_dir", str(tmp / "out"))
s.set("ui.confirm_settings", True)
s.set("audio.record_seconds", 10)
s.save()
eng = Engine(s)
panel = SettingsPanel(eng)


def on_disk(key):
    """What the saved file actually says, not what the object holds."""
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    node = raw
    for part in key.split("."):
        node = node.get(part, {})
    return node


print("=== a change is held, not written ===")
check("clean to begin with", not panel.is_dirty())
panel._save("audio.record_seconds", 25)
check("the panel is now dirty", panel.is_dirty())
check("the settings object is untouched",
      s.get("audio.record_seconds") == 10, str(s.get("audio.record_seconds")))
check("and so is the file on disk", on_disk("audio.record_seconds") == 10,
      str(on_disk("audio.record_seconds")))
check("but the pending value is known",
      panel.pending_value("audio.record_seconds") == 25)

print("\n=== undoing a change by hand puts the bar away ===")
# Setting a value back to what it was is the same as never having touched
# it, so there is nothing left to ask about.
panel._save("audio.record_seconds", 10)
check("no longer dirty", not panel.is_dirty())
panel._save("audio.record_seconds", 25)
check("dirty again after a fresh change", panel.is_dirty())

print("\n=== applying writes everything at once ===")
panel._save("speech.min_words", 5)
check("two changes pending", len(panel._pending) == 2)
panel.apply_pending()
check("no longer dirty", not panel.is_dirty())
check("the first landed", s.get("audio.record_seconds") == 25)
check("the second landed", s.get("speech.min_words") == 5)
check("and both reached disk",
      on_disk("audio.record_seconds") == 25
      and on_disk("speech.min_words") == 5)

print("\n=== cancelling puts the controls back ===")
before = panel.pending_value("audio.record_seconds")
panel._save("audio.record_seconds", 42)
panel._save("image.style_suffix", "something else entirely")
check("dirty", panel.is_dirty())
panel.cancel_pending()
check("clean again", not panel.is_dirty())
check("the value is unchanged", s.get("audio.record_seconds") == before)
check("and the widget shows the old value",
      panel.pending_value("audio.record_seconds") == before)

print("\n=== closing with changes in the air loses only those ===")
# Nothing was written, so a crash or a close is the same as cancelling.
panel._save("audio.cycle_seconds", 99)
check("pending before the close", panel.is_dirty())
check("the file still says the old value",
      on_disk("audio.cycle_seconds") != 99,
      str(on_disk("audio.cycle_seconds")))
reloaded = Settings.load(path=cfg)
check("a fresh load sees the last applied value",
      reloaded.get("audio.cycle_seconds") != 99)
panel.cancel_pending()

print("\n=== with the option off, changes apply immediately ===")
s.set("ui.confirm_settings", False)
s.save()
panel._save("speech.min_words", 7)
check("nothing is pending", not panel.is_dirty())
check("it went straight through", s.get("speech.min_words") == 7)
check("and straight to disk", on_disk("speech.min_words") == 7)
s.set("ui.confirm_settings", True)
s.save()

print("\n=== the bar and the window ===")
eng2 = Engine(Settings.load(path=cfg))
win = MainWindow(eng2)
win.resize(1100, 700)
win.show()
pump(0.3)

check("the bar exists", isinstance(win.change_bar, ChangeBar))
check("the vignette exists", isinstance(win.vignette, Vignette))
check("the bar starts hidden", not win.change_bar.is_showing())

win.show_panel("settings")
pump()
win.settings._save("audio.record_seconds", 31)
pump(0.5)
check("a change brings the bar up", win.change_bar.is_showing())
check("it is on screen", win.change_bar.isVisible())
check("and sits near the bottom of the window",
      win.change_bar.y() > win.height() * 0.7,
      f"y={win.change_bar.y()} of {win.height()}")

print("\n  navigating away is refused:")
moved = win.show_panel("live")
pump(0.3)
check("the move was refused", moved is False)
check("still on Settings",
      win._page_keys[win.stack.currentIndex()] == "settings")
check("the vignette flashed", win.vignette.isVisible())
check("and the bar is still up", win.change_bar.is_showing())

print("\n  applying lets you leave:")
win.change_bar.apply_btn.click()
pump(0.4)
check("the change landed", eng2.s.get("audio.record_seconds") == 31)
check("no longer dirty", not win.settings.is_dirty())
check("navigation works again", win.show_panel("live") is True)
check("and we actually moved",
      win._page_keys[win.stack.currentIndex()] == "live")

print("\n  cancelling also lets you leave:")
win.show_panel("settings")
pump()
win.settings._save("speech.min_words", 11)
pump(0.3)
check("dirty again", win.settings.is_dirty())
check("blocked again", win.show_panel("gallery") is False)
win.change_bar.cancel_btn.click()
pump(0.4)
check("clean after cancelling", not win.settings.is_dirty())
check("the value was not written", eng2.s.get("speech.min_words") != 11)
check("navigation restored", win.show_panel("gallery") is True)

print("\n=== the glow says what state things are in ===")
# Red while unresolved, green only once Apply has actually been pressed.
bar = win.change_bar


def glow_colour():
    return bar._glow.color().name().upper()


win.show_panel("settings")
pump()
win.settings._save("audio.cycle_seconds", 44)
pump(0.5)
check("the bar is up", bar.is_showing())
check("it glows red while unapplied",
      glow_colour() == theme.DANGER.upper(), glow_colour())
check("and the glow is actually on", bar._glow.blurRadius() > 5,
      f"radius {bar._glow.blurRadius():.0f}")

print("\n  being refused does not turn it green:")
win.show_panel("live")
pump(0.3)
check("still red after a refusal",
      glow_colour() == theme.DANGER.upper(), glow_colour())

print("\n  cancelling keeps it red:")
bar.cancel_btn.click()
pump(0.15)
check("red on the way out",
      glow_colour() == theme.DANGER.upper(), glow_colour())
pump(1.2)

print("\n  applying turns it green:")
win.show_panel("settings")
pump()
win.settings._save("audio.cycle_seconds", 45)
pump(0.5)
check("red again for the new change",
      glow_colour() == theme.DANGER.upper(), glow_colour())
bar.apply_btn.click()
pump(0.15)
check("green once applied",
      glow_colour() == theme.OK.upper(), glow_colour())
check("the change landed", eng2.s.get("audio.cycle_seconds") == 45)
pump(1.2)
check("and the bar is gone", not bar.is_showing())

eng.shutdown()
eng2.shutdown()
shutil.rmtree(tmp, ignore_errors=True)



print("\n=== the capture picker keeps its selection ===")
# The picker is the only control whose item data is a tuple, and
# QComboBox.findData compares wrapped Python objects by identity - so a
# tuple rebuilt from the settings never matched, and the picker silently
# fell back to the first entry.
from avgui.settings_panel import index_of

dev = Path("_devicetest").resolve()
shutil.rmtree(dev, ignore_errors=True)
dev.mkdir(parents=True, exist_ok=True)
sd = Settings.load(path=dev / "s.json")
sd.set("ui.confirm_settings", True)
sd.save()

# Selection persistence is a widget/settings contract, not a hardware test.
# Give the picker two deterministic outputs so a clean CI machine with no
# audio endpoints exercises the same tuple-valued itemData path.
import avcore.audio as _audio_module
_real_list_devices = _audio_module.list_devices
_audio_module.list_devices = lambda _kind=None: [
    {"name": "Test Speakers A", "kind": "output",
     "is_default": True, "index": 101},
    {"name": "Test Speakers B", "kind": "output",
     "is_default": False, "index": 102},
]

engd = Engine(sd)
pd = SettingsPanel(engd)

outputs = [i for i in range(pd.device.count())
           if isinstance(pd.device.itemData(i), tuple)
           and pd.device.itemData(i)[1] == "output"
           and pd.device.itemData(i)[0]]
check("there are two outputs to choose from", len(outputs) >= 2,
      f"{len(outputs)}")

first, second = outputs[0], outputs[1]
name_first = pd.device.itemData(first)[0]

print("\n  picking one and applying:")
pd.device.setCurrentIndex(first)
pd.apply_pending()
check("the setting was written", sd.get("audio.device_name") == name_first,
      sd.get("audio.device_name"))
check("the picker still shows it", pd.device.currentIndex() == first)

print("\n  picking another and cancelling:")
pd.device.setCurrentIndex(second)
check("a change is pending", pd.is_dirty())
pd.cancel_pending()
check("it went back to the applied one, not the default",
      pd.device.currentIndex() == first,
      f"index {pd.device.currentIndex()}, wanted {first}")
check("and the setting is untouched",
      sd.get("audio.device_name") == name_first)

print("\n  a fresh panel opens on the saved device:")
pd2 = SettingsPanel(engd)
check("the saved device is selected, not the first entry",
      pd2.device.currentData() == (name_first, "output"),
      str(pd2.device.currentData()))

print("\n  refreshing the app list keeps the selection:")
pd2._refresh_apps()
check("still on the saved device",
      pd2.device.currentData() == (name_first, "output"),
      str(pd2.device.currentData()))

print("\n  the lookup helper itself:")
stored = pd2.device.itemData(first)
rebuilt = (stored[0], stored[1])
check("an equal tuple is found", index_of(pd2.device, rebuilt) == first)
check("Qt's own findData does not find it",
      pd2.device.findData(rebuilt) == -1,
      "this is the bug being worked around")
check("something absent returns -1",
      index_of(pd2.device, ("nothing", "output")) == -1)

engd.shutdown()
shutil.rmtree(dev, ignore_errors=True)

print("\n=== saved profiles ===")
# A profile is somewhere to keep "how I like it for streaming", so the
# alternative to twenty adjustments is one click.
import avcore.profiles as profiles
from avgui.dialogs import (
    ConfirmProfile, ConfirmProfileDelete, ConfirmReset, NameProfile,
)

profiles.PROFILES_FILE = tmp / "profiles.json"

sp = Settings.load(path=tmp / "profiles_settings.json")
sp.set("comfyui.path", str(tmp / "SomeComfy"))
sp.set("models.civitai_key", "a-secret")
sp.set("image.width", 1920)
sp.set("speech.min_words", 7)
sp.save()

profiles.save("Streaming", sp)
check("it is listed", profiles.names() == ["Streaming"],
      str(profiles.names()))

print("\n  three things a profile refuses to carry:")
stored = profiles._read()["Streaming"]
check("not the API key", "models.civitai_key" not in stored,
      "credentials should not ride along in a file people share")
check("not where ComfyUI is", "comfyui.path" not in stored,
      "that describes the machine, not a preference")
check("not the folders",
      not any(key.startswith("paths.") for key in stored))
check("but the preferences are all there",
      stored["image.width"] == 1920 and stored["speech.min_words"] == 7)

print("\n  loading puts them back:")
sp.set("image.width", 512)
sp.set("speech.min_words", 2)
sp.save()
applied = profiles.apply("Streaming", sp)
check("the settings return", sp.get("image.width") == 1920
      and sp.get("speech.min_words") == 7)
check("and it says how many", applied > 40, str(applied))
check("the API key is left alone",
      sp.get("models.civitai_key") == "a-secret",
      "a profile that cleared it would be a nasty surprise")

print("\n  a profile saved before a setting existed:")
old_shape = profiles._read()
old_shape["Sparse"] = {"image.width": 800}
profiles._write(old_shape)
sp.set("speech.min_words", 5)
sp.save()
profiles.apply("Sparse", sp)
check("what it carries is applied", sp.get("image.width") == 800)
check("what it never knew about is left alone",
      sp.get("speech.min_words") == 5,
      "reverting to a default it never stored would be wrong")

print("\n  deleting:")
check("it goes", profiles.delete("Sparse") and
      "Sparse" not in profiles.names())
check("deleting one that is not there is not an error",
      profiles.delete("Sparse") is False)

print("\n=== resetting to defaults ===")
sr = Settings.load(path=tmp / "reset_settings.json")
sr.set("comfyui.path", str(tmp / "KeepThis"))
sr.set("image.width", 640)
sr.set("ui.decor_theme", "cryptic")
sr.set("paths.overlay_dir", str(tmp / "KeepThisToo"))
sr.save()

changed = profiles.reset_to_defaults(sr)
check("settings go back to the defaults",
      sr.get("image.width") == DEFAULTS["image"]["width"]
      and sr.get("ui.decor_theme") == "none")
check("and it says how many changed", changed >= 2, str(changed))
check("where ComfyUI lives is kept",
      sr.get("comfyui.path") == str(tmp / "KeepThis"),
      "a reset that loses a 10 GB install is a bigger promise than the "
      "button makes")
check("and so are the folders",
      sr.get("paths.overlay_dir") == str(tmp / "KeepThisToo"))

print("\n  the dialogs say what will happen:")
dlg = ConfirmReset()
words = " ".join(l.text() for l in dlg.findChildren(QLabel))
check("reset says images are safe", "images" in words and "kept" in words)
check("reset suggests saving first", "profile first" in words)
check("and keeping is the default",
      {b.objectName(): b for b in dlg.findChildren(QPushButton)}
      ["denyButton"].isDefault())
dlg.reject()

dlg = ConfirmProfile("Streaming")
words = " ".join(l.text() for l in dlg.findChildren(QLabel))
check("loading warns about unsaved settings", "will be lost" in words)
check("and says what is not touched", "not affected" in words)
dlg.reject()

dlg = NameProfile(["Streaming"], "Streaming")
check("naming an existing profile offers to replace",
      dlg.go.text() == "Replace", dlg.go.text())
dlg.name.setText("Something new")
check("a new name saves", dlg.go.text() == "Save")
dlg.name.setText("   ")
check("an empty name cannot be saved", not dlg.go.isEnabled())
dlg.reject()

dlg = ConfirmProfileDelete("Streaming")
words = " ".join(l.text() for l in dlg.findChildren(QLabel))
check("deleting a profile says current settings stay",
      "stay exactly as they are" in words)
dlg.reject()

print("\n=== safe mode is visible in the Avoid row ===")
# The safety terms are added at generation rather than written into the
# field, so that turning safe mode off returns exactly the wording
# somebody chose. Right, and completely invisible - which was reported
# as the tick doing nothing at all.
from avcore.safety import NEGATIVE_TERMS

check("there is a note under the Avoid row",
      hasattr(panel, "avoid_note"))

s.set("safety.safe_mode", False)
panel._sync_avoid_note()
check("nothing is said while it is off",
      panel.avoid_note.text() == "")

s.set("safety.safe_mode", True)
panel._sync_avoid_note()
check("and the terms are listed while it is on",
      NEGATIVE_TERMS in panel.avoid_note.text(),
      panel.avoid_note.text()[:50])
check("said as an addition, not as a change to the field",
      "also avoids" in panel.avoid_note.text())

_field = panel._widgets["image.negative_prompt"]
check("the field itself is never edited",
      _field.text() == s.get("image.negative_prompt"),
      "unticking must give back what was typed, unaltered")

print("\n  it follows Apply rather than the tick:")
import inspect as _inspect

_effects = _inspect.getsource(panel.__class__._run_side_effects)
check("the note is refreshed when settings land",
      "_sync_avoid_note" in _effects,
      "the tick is held by the confirm bar, so following the tick "
      "would show the note before the setting existed")

s.set("safety.safe_mode", False)
panel._sync_avoid_note()

bad = [n for n, ok in results if not ok]



print("\n=== every field reverts when cancelled ===")
# Driven through the widgets themselves, not through _save, because the
# bug being guarded against was a control that kept its new value while
# the setting underneath went back.
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox,
)

audit = Path("_audittest").resolve()
shutil.rmtree(audit, ignore_errors=True)
audit.mkdir(parents=True, exist_ok=True)
sa = Settings.load(path=audit / "s.json")
sa.set("ui.confirm_settings", True)
sa.save()
enga = Engine(sa)
pa = SettingsPanel(enga)

print(f"  {len(pa._widgets)} fields registered")


def read(widget):
    if isinstance(widget, QComboBox):
        return widget.currentIndex()
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QLineEdit):
        return widget.text()
    return None


def nudge(widget):
    """Move a control to some other value, whatever kind it is."""
    if isinstance(widget, QComboBox):
        if widget.count() < 2:
            return False
        # Prefer an entry carrying data; fall back to any real entry, so
        # a combo built with plain addItems is still driveable. Skip
        # separators, which have no text and cannot be selected.
        for want_data in (True, False):
            for i in range(widget.count()):
                if i == widget.currentIndex():
                    continue
                has_data = widget.itemData(i) is not None
                if has_data != want_data or not widget.itemText(i).strip():
                    continue
                widget.setCurrentIndex(i)
                return True
        return False
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        step = 1 if isinstance(widget, QSpinBox) else 0.5
        target = widget.value() + step
        if target > widget.maximum():
            target = widget.value() - step
        widget.setValue(target)
        return True
    if isinstance(widget, QCheckBox):
        widget.setChecked(not widget.isChecked())
        return True
    if isinstance(widget, QLineEdit):
        # Already stripped: _save strips before storing, so an untrimmed
        # value would differ from what comes back and look like a fault
        # in the restore rather than in this test.
        widget.setText(((widget.text() or "") + " changed").strip())
        widget.editingFinished.emit()
        return True
    return False


reverted, skipped, broken = [], [], []
for key, widget in sorted(pa._widgets.items()):
    original_widget = read(widget)
    original_setting = sa.get(key)
    if not nudge(widget):
        skipped.append(key)
        continue
    if not pa.is_dirty():
        # Moving the control produced no pending change at all.
        broken.append((key, "no change recorded"))
        continue
    pa.cancel_pending()
    if read(widget) != original_widget:
        broken.append((key, f"widget stayed at {read(widget)!r}"))
    elif sa.get(key) != original_setting:
        broken.append((key, "setting was written"))
    else:
        reverted.append(key)

print(f"  reverted correctly : {len(reverted)}")
print(f"  not drivable here  : {len(skipped)}  {skipped if skipped else ''}")
for key, why in broken:
    print(f"     *** {key}: {why}")
check("every drivable field reverts on cancel", not broken,
      f"{len(broken)} broken" if broken else f"{len(reverted)} checked")

print("\n  and the same fields survive apply-then-cancel:")
# The reported bug: apply a value, pick another, cancel - it must go back
# to the applied value, not to the default.
second_round = []
for key, widget in sorted(pa._widgets.items()):
    if key in skipped:
        continue
    if key == "ui.confirm_settings":
        # Applying this one off turns the whole mechanism off, so later
        # changes correctly land immediately. Checked on its own below.
        continue
    if not nudge(widget):
        continue
    pa.apply_pending()
    applied_widget = read(widget)
    applied_setting = sa.get(key)
    if not nudge(widget):
        continue
    pa.cancel_pending()
    if read(widget) != applied_widget or sa.get(key) != applied_setting:
        second_round.append(key)

check("cancelling returns to the applied value, not the default",
      not second_round, ", ".join(second_round) or "all good")

print("\n  turning the confirm option off applies straight away:")
sa.set("ui.confirm_settings", True)
sa.save()
pb = SettingsPanel(enga)
pb.confirm_check.setChecked(False)
check("turning it off is itself held back", pb.is_dirty())
pb.apply_pending()
check("now off", sa.get("ui.confirm_settings") is False)
pb._save("audio.cycle_seconds", 33)
check("later changes are not held", not pb.is_dirty())
check("and land immediately", sa.get("audio.cycle_seconds") == 33)

# Restore the real enumerator only after every SettingsPanel audit has used
# the deterministic device fixture.
_audio_module.list_devices = _real_list_devices
enga.shutdown()
shutil.rmtree(audit, ignore_errors=True)

bad = [n for n, ok in results if not ok]

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("settings changes wait to be applied")
