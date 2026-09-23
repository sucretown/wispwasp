"""
Layout switching test.

The claim being checked is that switching reparents the same panel objects
rather than rebuilding them, and that no widget gets destroyed by Qt's
parent ownership along the way.
"""
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from avcore.config import Settings
from avcore.engine import Engine
from avgui import theme
from avgui.window import MainWindow
from demo_stubs import install_stubs

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


app = QApplication([])
app.setStyleSheet(theme.QSS)

s = Settings.load()
s.set("ui.layout", "hybrid")
eng = Engine(s)
install_stubs(eng, autostart=True)
win = MainWindow(eng)
win.resize(1200, 760)
win.show()
eng.start()


def pump(seconds):
    """Let Qt process events without blocking the loop."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(predicate, seconds=30):
    """
    Wait for something to happen rather than guessing how long it takes.

    A fixed sleep here was marginal: a demo cycle is a 3s clip plus a 2.4s
    render, so seven seconds passed on an idle machine and failed when the
    build was compiling in the background.
    """
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        app.processEvents()
        time.sleep(0.05)
    return False


print("=== panels are created once ===")
ids = {k: id(p) for k, p in win._panels.items()}
check("started in hybrid", win.layout_name == "hybrid")
# A machine without ComfyUI opens on Setup, so the Live page is asked for
# explicitly rather than assumed to be showing.
win.show_panel("live")
pump(0.2)
check("prompt strip visible in hybrid", win.live.strip.isVisible())

wait_for(lambda: eng.snapshot().get("image"))
first_image = eng.snapshot().get("image")
check("engine produced an image before switching",
      first_image is not None)

print("\n=== cycling every layout twice ===")
order = ["sidebar", "split", "hybrid", "split", "sidebar", "hybrid"]
for name in order:
    win.set_layout(name)
    pump(0.6)
    same = all(id(win._panels[k]) == ids[k] for k in ids)
    # Touching a widget whose C++ side was destroyed raises RuntimeError,
    # which is exactly the failure this test exists to catch.
    try:
        win.live.status.text()
        win.prompt.box.toPlainText()
        win.gallery.count.text()
        win.settings.suffix.text()
        alive = True
    except RuntimeError as exc:
        alive = False
        print(f"      widget destroyed: {exc}")
    check(f"{name}: same objects reused, all widgets alive", same and alive)

print("\n=== layout-specific structure ===")
# Page counts include Customize, which has no nav entry of its own - it
# unrolls under Settings - but still needs a page in the stack.
win.set_layout("hybrid")
pump(0.3)
check("hybrid shows the strip", win.live.strip.isVisible())
# Named rather than counted: the count changed when About was
# added, and a test that fails because a page exists tells nobody
# anything useful.
check("hybrid nav lists every panel",
      {"live", "prompt", "gallery", "setup", "settings"}
      <= set(win._page_keys), str(win._page_keys))

win.set_layout("sidebar")
pump(0.3)
check("panels layout hides the strip", not win.live.strip.isVisible())
check("panels nav lists every panel",
      {"live", "prompt", "gallery", "setup", "settings"}
      <= set(win._page_keys), str(win._page_keys))

win.set_layout("split")
pump(0.3)
check("split hides the strip", not win.live.strip.isVisible())
check("split merges live and prompt into one entry",
      "prompt" not in win._page_keys
      and "live" in win._page_keys, str(win._page_keys))
check("every layout carries the Customize sub-page",
      "customize" in win._page_keys)
check("split built a splitter", win.splitter is not None)
check("splitter holds live and prompt", win.splitter.count() == 2)

print("\n=== nothing was lost across the switches ===")
check("overlay image survived", eng.snapshot().get("image") is not None)
check("still listening", eng.snapshot().get("listening") is True)
wait_for(lambda: eng.snapshot().get("generated", 0) >= 1)
snap = eng.snapshot()
check("engine kept generating through the switches",
      snap.get("generated", 0) >= 1)
# The preview follows the engine's state through a signal, so it can be
# a beat behind the snapshot taken a line earlier. Waiting for it to
# catch up tests that it arrives; comparing immediately tested how
# quickly the machine happened to deliver a queued signal, which failed
# one build in several for no reason anybody could act on.
wait_for(lambda: win.live.preview._path == snap.get("image"))
check("live panel is showing the current image",
      win.live.preview._path == snap.get("image"),
      f"preview={win.live.preview._path} state={snap.get('image')}")

print("\n=== a dragged splitter size is remembered ===")
win.set_layout("split")
pump(0.6)
initial = win.splitter.sizes()
print(f"    default split: {initial}")
# Guard against the whole check passing on zeroes, which would verify
# nothing at all.
check("splitter actually has a width", sum(initial) > 100)
check("live side gets the larger share by default",
      initial[0] > initial[1])

win.splitter.setSizes([760, 340])
pump(0.4)
settled = win.splitter.sizes()
print(f"    after dragging: {settled}")
win.set_layout("hybrid")
pump(0.4)
win.set_layout("split")
pump(0.6)
restored = win.splitter.sizes()
print(f"    after a round trip: {restored}")
check("splitter kept its proportions",
      sum(restored) > 100 and abs(restored[0] - settled[0]) < 20)

eng.shutdown()
print("\n=== quick settings above the prompt box ===")
# Changing the model or the size is part of working, not part of
# configuring, so the few settings people touch most sit beside the
# picture. They write immediately - there is no Apply here, and none of
# them needs one.
from avcore.catalog import model_label
from avgui.panels import _backend_and_model

# One strip per prompt box now, rather than one in the side bar. The
# earlier tests leave the window in whichever layout they finished with,
# and Panels hides the Live prompt strip entirely - a widget inside a
# hidden parent reports itself invisible however healthy it is.
win.set_layout("hybrid")
win.show_panel("live")
pump(0.5)
quick = win.live.quick
check("they sit above the prompt box", quick.isVisible())
check("the Prompt page has one of its own",
      hasattr(win.prompt, "quick"),
      "both pages behave the same way")
check("and the layout picker stays in the side bar",
      win.layout_pick.isVisible(),
      "that one is about the window, not about the next image")

print("\n  a change here lands immediately:")
before = (s.get("image.width"), s.get("image.height"))
for index in range(quick.size.count()):
    if quick.size.itemData(index) != before:
        quick.size.setCurrentIndex(index)
        break
pump(0.3)
after = (s.get("image.width"), s.get("image.height"))
check("the size is written straight away", after != before,
      f"{before} -> {after}")

quick.steps.setValue(11)
pump(0.2)
check("and so are the steps", s.get("comfyui.steps") == 11)

print("\n  Pollinations hides what it cannot use:")
quick.backend.setCurrentIndex(quick.backend.findData("pollinations"))
pump(0.4)
check("the backend is saved", s.get("image.backend") == "pollinations")
check("the model picker goes", not quick.model.isVisible())
check("steps go", not quick.steps.isVisible())
check("guidance goes", not quick.cfg.isVisible())
check("size stays, because it still applies", quick.size.isVisible())
check("and it says why", "online" in quick.note.text().lower())

quick.backend.setCurrentIndex(quick.backend.findData("comfyui"))
pump(0.4)
check("switching back brings them out", quick.model.isVisible())

print("\n  the two strips agree with each other:")
quick.steps.setValue(14)
pump(0.4)
check("a change on Live reaches the Prompt page",
      win.prompt.quick.steps.value() == 14,
      "three views of the same settings, which must never disagree")

print("\n  the side bar and the settings page agree:")
s.set("comfyui.steps", 33)
s.save()
quick.refresh()
pump(0.2)
check("a change made elsewhere shows here", quick.steps.value() == 33,
      "the two read the same settings, so they cannot disagree")

print("\n  an unusual size set in Settings is not overwritten:")
s.set("image.width", 1152)
s.set("image.height", 896)
s.save()
quick.refresh()
pump(0.2)
check("it is offered as it stands", quick.size.currentData() == (1152, 896),
      str(quick.size.currentText()))
check("and the settings are untouched",
      s.get("image.width") == 1152 and s.get("image.height") == 896,
      "opening a panel must not quietly change a setting")

print("\n  the layout can be changed from here:")
start = s.get("ui.layout")
other = "split" if start != "split" else "hybrid"
win.layout_pick.setCurrentIndex(win.layout_pick.findData(other))
pump(0.8)
check("the setting follows", s.get("ui.layout") == other,
      f"{start} -> {s.get('ui.layout')}")
# Which strip is on screen depends on the layout: Split gives the
# prompt box to the Prompt panel, so the Live strip is correctly
# hidden there. What matters is that the controls are still reachable
# wherever the prompt box ended up.
on_screen = [panel.quick for panel in (win.live, win.prompt)
             if getattr(panel, "quick", None) is not None
             and panel.quick.isVisible()]
check("the controls are still with the prompt box", bool(on_screen),
      f"{len(on_screen)} strip(s) visible in {win.engine.s.get('ui.layout')}"
      if hasattr(win, "s") else f"{len(on_screen)} visible")
check("and the picker shows where it is",
      win.layout_pick.currentData() == other)

print("\n=== what made each image ===")
check("a local model is named by its file",
      model_label("comfyui", "juggernautXL_ragnarok.safetensors")
      == "ComfyUI - juggernautXL_ragnarok",
      "the extension is noise")
check("online needs no checkpoint",
      model_label("pollinations", "") == "Pollinations")
check("and an old image says so plainly",
      model_label("", "") == "model not recorded",
      "images made before this was recorded")

s.set("image.backend", "comfyui")
s.set("comfyui.checkpoint", "some_model.safetensors")
check("the panels show what is generating",
      _backend_and_model(s) == "some_model")
s.set("comfyui.checkpoint", "")
check("and say so when nothing is pinned",
      _backend_and_model(s) == "first model found",
      "beats showing nothing, and beats pretending to know")

print("\n=== the hybrid strip offers the same choices as the Prompt page ===")
# Typing into the strip used to be a lesser version of typing on the
# Prompt page: no way to say whether the saved style applied, which one,
# or whether the result went straight to the overlay.
win.set_layout("hybrid")
win.show_panel("live")
pump(0.5)

check("the saved style can be turned off here",
      hasattr(win.live, "use_suffix"))
check("the style itself can be chosen",
      hasattr(win.live, "style_pick")
      and win.live.style_pick.count() > 0,
      f"{win.live.style_pick.count()} styles")
check("and whether it lands on the overlay",
      hasattr(win.live, "to_overlay"))

print("\n  the two style pickers are one decision:")
names = [win.live.style_pick.itemData(i)
         for i in range(win.live.style_pick.count())]
if len(names) > 1:
    other = [n for n in names if n != win.live.style_pick.currentData()][0]
    win.live.style_pick.setCurrentIndex(
        win.live.style_pick.findData(other))
    pump(0.6)
    check("choosing on Live reaches the Prompt page",
          win.prompt.style_pick.currentData() == other,
          f"{other} on both")
    check("and the setting agrees",
          s.get("image.style_name") == other)

print("\n  the overlay choice is remembered, not just ticked:")
win.live.to_overlay.setChecked(False)
pump(0.3)
check("turning it off is written", s.get("image.manual_auto_push") is False)
win.live.to_overlay.setChecked(True)
pump(0.3)
check("and turning it back on", s.get("image.manual_auto_push") is True)

check("the tick says what the style adds",
      "Adds:" in win.live.use_suffix.toolTip(),
      win.live.use_suffix.toolTip())

print("\n=== the divider does not fight the picture ===")
# A QLabel reports its size hints from whatever pixmap it holds, and the
# preview replaces its pixmap on every resize. So the layout asked how
# big it wanted to be, the answer changed, the layout resized it, and
# round again - which in the split layout looked like the divider
# jumping about under the hand as soon as an image was on screen.
import shutil as _shutil

from avgui.widgets import ImagePreview
from demo_stubs import write_png

# This suite runs against the real settings, so the pictures it needs
# go somewhere of their own and are cleared up afterwards.
drag_tmp = Path("_dragcheck")
_shutil.rmtree(drag_tmp, ignore_errors=True)
drag_tmp.mkdir(parents=True, exist_ok=True)

win.set_layout("split")
win.show_panel("live")
pump(0.6)

preview = win.live.preview
empty_min = preview.minimumSizeHint().width()
check("the preview has a modest floor to start with",
      empty_min <= 400, f"{empty_min}px")


def travel():
    splitter = win.splitter
    total = sum(splitter.sizes())
    splitter.setSizes([80, total - 80])
    pump(0.2)
    left = splitter.sizes()[0]
    splitter.setSizes([total - 80, 80])
    pump(0.2)
    right = splitter.sizes()[0]
    return left, right


before = travel()

shot = drag_tmp / "wide.png"
write_png(shot, 1920, 1080, (190, 130, 90))
eng.publish_overlay(shot, source="manual")
pump(1.0)

check("a picture does not raise the preview's floor",
      preview.minimumSizeHint().width() == empty_min,
      f"{preview.minimumSizeHint().width()}px, was {empty_min}px")

after = travel()
check("nor take away any of the divider's travel", after == before,
      f"{before} then {after}")

huge = drag_tmp / "huge.png"
write_png(huge, 3000, 3000, (90, 140, 190))
eng.publish_overlay(huge, source="manual")
pump(1.0)
check("and a very large one changes nothing either",
      preview.minimumSizeHint().width() == empty_min
      and travel() == before,
      "the bigger the picture, the worse it used to get")

_shutil.rmtree(drag_tmp, ignore_errors=True)

check("the picture is still drawn",
      preview.pixmap() is not None and not preview.pixmap().isNull())

print("\n  the divider has somewhere to go:")
left, right = travel()
check("it is not pinned to a narrow band", right - left > 400,
      f"{right - left}px of travel")
check("and the panel can be made genuinely narrow", left < 500,
      f"{left}px - the status bar hides what does not fit")

print("\n=== choosing which half of Stable Diffusion to use ===")
# The family decides what can be listed under it and what a LoRA has to
# be to work, so it sits in front of the model rather than in Setup.
from avcore.setup import models_for_tier

win.set_layout("hybrid")
win.show_panel("live")
pump(0.5)
_quick = win.live.quick

check("the strip offers both",
      [_quick.family.itemData(i) for i in range(_quick.family.count())]
      == ["sd15", "sdxl"])

s.set("models.tier", "sd15")
_quick.refresh()
pump(0.3)
_listed = [_quick.model.itemData(i) for i in range(_quick.model.count())
           if _quick.model.itemData(i)]
_allowed = {p.name for p in models_for_tier("sd15", s)}
check("only models of that family are listed",
      set(_listed) <= _allowed,
      f"{len(_listed)} listed, {len(_allowed)} of that family")

_quick.family.setCurrentIndex(_quick.family.findData("sdxl"))
pump(0.5)
check("switching writes the choice", s.get("models.tier") == "sdxl")
_listed = [_quick.model.itemData(i) for i in range(_quick.model.count())
           if _quick.model.itemData(i)]
_allowed = {p.name for p in models_for_tier("sdxl", s)}
check("and the list follows it", set(_listed) <= _allowed,
      f"{_listed}")

print("\n  a model from the other family is not left selected:")
s.set("models.tier", "sd15")
s.set("comfyui.checkpoint", "something_sdxl.safetensors")
_quick.refresh()
pump(0.2)
_quick.family.setCurrentIndex(_quick.family.findData("sdxl"))
pump(0.5)
_quick.family.setCurrentIndex(_quick.family.findData("sd15"))
pump(0.5)
check("it is cleared rather than left pointing at nothing loadable",
      (s.get("comfyui.checkpoint") or "") == "",
      f"{s.get('comfyui.checkpoint')!r} - blank means first one found")

print("\n  and the Prompt page shows the same choice:")
check("both strips agree",
      win.prompt.quick.family.currentData()
      == win.live.quick.family.currentData(),
      "they read the same setting")

print("\n  the LoRA popup can fetch more:")
# Finding out you have none, and being able to do something about it,
# belong in the same window - otherwise the popup's only message to
# somebody with an empty folder is "go elsewhere".
import inspect as _inspect

from avgui.quick_settings import QuickSettings

_popup_source = _inspect.getsource(QuickSettings._open_loras)
check("the popup offers Get more", "Get more" in _popup_source)
check("which opens the same browser Settings uses",
      "ModelBrowser" in _inspect.getsource(QuickSettings._get_loras)
      and 'kind="LORA"'
      in _inspect.getsource(QuickSettings._get_loras),
      "one browser, not a second copy of it")
check("and the list is rebuilt when it closes",
      "chooser.reload()" in _inspect.getsource(QuickSettings._get_loras),
      "a LoRA that has just arrived should be tickable without closing "
      "the popup and opening it again")

print("\n=== two buttons that did nothing ===")
# Both were the same shape: something failing inside a slot, where Qt
# swallows the exception and the control simply looks dead.
import inspect as _inspect

from avgui.model_browser import ModelBrowser

_signature = _inspect.signature(ModelBrowser.__init__)
_kind = _signature.parameters.get("kind")
check("the browser's kind is keyword-only",
      _kind is not None and _kind.kind is _inspect.Parameter.KEYWORD_ONLY,
      "it was added in second place, where callers had been passing a "
      "parent positionally - so Settings handed it a widget as the kind")

_panel_source = Path("avgui/settings_panel.py").read_text(encoding="utf-8")
check("nothing calls it positionally any more",
      "ModelBrowser(self.s, self)" not in _panel_source,
      "the call that made Get more models do nothing")
check("and every call names its arguments",
      _panel_source.count("ModelBrowser(self.s, parent=")
      + _panel_source.count('ModelBrowser(self.s, kind=') >= 2)

print("\n  leaving the gallery takes the clip player with you:")
_window_source = Path("avgui/window.py").read_text(encoding="utf-8")
check("switching panel closes it",
      "_close_clip" in _window_source,
      "it covers the gallery like the image viewer, and was left "
      "playing over whatever came next")
check("beside the viewer it sits with",
      _window_source.index("_close_clip")
      > _window_source.index("viewer.collapse"),
      "the same rule, in the same place")

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("layout switching is safe")
