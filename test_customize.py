"""
The Customize sub-panel: its reveal, and the adjustable palette.

The palette is the interesting part to test - a colour scheme that
applies to new widgets but not existing ones, or that fails to survive a
restart, would look like it worked until it plainly did not.
"""
import shutil
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from avcore.config import Settings
from avcore.engine import Engine
from avgui import theme
from avgui.customize_panel import PRESETS, CustomizePanel
from avgui.overlays import SubNavItem
from avgui.window import MainWindow

app = QApplication.instance() or QApplication([])
theme.apply_to(app, None)

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


tmp = Path("_customtest").resolve()
# Windows will not delete a folder a live window still has open, so the
# cleanup at the end can quietly fail. Tolerating a leftover here is the
# difference between one flaky run and every subsequent run failing.
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir(parents=True, exist_ok=True)
cfg = tmp / "settings.json"
s = Settings.load(path=cfg)
s.set("paths.overlay_dir", str(tmp / "out"))
s.save()
eng = Engine(s)

print("=== the palette is rebuildable ===")
before = theme.build_qss()
theme.set_palette(main="#7C5CE0", accent="#E08A2B")
after = theme.build_qss()
check("the stylesheet changes with the palette", before != after)
check("the main colour reaches it", "#7C5CE0" in after)
check("derived shades were recomputed",
      theme.SELECTION == theme.mix(theme.BG, "#E08A2B", 0.45),
      theme.SELECTION)

print("\n  status colours are deliberately fixed:")
# Red means live and amber means working. If those could be repainted the
# interface could be made to lie about what it is doing.
check("the tally red did not move", theme.TALLY == "#E0392B")
check("the working amber did not move", theme.WORKING == "#F0A32E")
check("the ok green did not move", theme.OK == "#4FB286")

theme.set_palette(main=theme.DEFAULT_MAIN, accent=theme.DEFAULT_ACCENT)
check("it can be put back", theme.MAIN == theme.DEFAULT_MAIN)

print("\n  colours picked at runtime are read when drawing:")
from avgui.theme import kind_colour
theme.set_palette(accent="#E08A2B")
check("a typed entry follows the accent",
      kind_colour("typed") == "#E08A2B", kind_colour("typed"))
check("a skipped entry still uses the tally red",
      kind_colour("skipped") == theme.TALLY)
theme.set_palette(accent=theme.DEFAULT_ACCENT)

print("\n  readable text is chosen for the button colour:")
check("white on a dark main", theme.readable_on("#2B2B60") == "#FFFFFF")
check("dark on a pale main", theme.readable_on("#F0E6A0") == "#10161C")

print("\n=== the Customize panel ===")
panel = CustomizePanel(eng)
check("it offers a main colour", panel.main_swatch is not None)
check("and an accent colour", panel.accent_swatch is not None)
check("they start from the defaults",
      panel.main_swatch.colour() == theme.DEFAULT_MAIN,
      panel.main_swatch.colour())
check("there are starting points to choose from", len(PRESETS) >= 4,
      f"{len(PRESETS)} presets")

print("\n  choosing a preset saves it:")
name, main, accent = PRESETS[2]
panel._use_preset(main, accent)
check("the main colour was stored", s.get("ui.theme_main") == main,
      s.get("ui.theme_main"))
check("the accent was stored", s.get("ui.theme_accent") == accent)
check("the swatches followed", panel.main_swatch.colour() == main)

print("\n  and it survives a restart:")
fresh = Settings.load(path=cfg)
check("the file kept it", fresh.get("ui.theme_main") == main,
      fresh.get("ui.theme_main"))
theme.apply_to(app, fresh)
check("applying it on load sets the palette", theme.MAIN == main,
      theme.MAIN)

print("\n  resetting goes back to the originals:")
panel._use_preset(theme.DEFAULT_MAIN, theme.DEFAULT_ACCENT)
check("main is back", s.get("ui.theme_main") == theme.DEFAULT_MAIN)
check("accent is back", s.get("ui.theme_accent") == theme.DEFAULT_ACCENT)

print("\n=== the sub-panel unrolls under Settings ===")
win = MainWindow(eng)
win.resize(1100, 700)
win.show()
pump(0.4)

nav = win.customize_nav
check("it is a sub-nav item", isinstance(nav, SubNavItem))
check("it starts rolled up", not nav.is_open())
check("with no height", nav.maximumHeight() == 0)

win.show_panel("settings")
pump(0.6)
check("opening Settings unrolls it", nav.is_open())
check("it has real height now", nav.maximumHeight() > 20,
      f"{nav.maximumHeight()}px")

print("\n  it leads to its own page:")
check("navigating to it works", win.show_panel("customize") is True)
check("the Customize page is showing",
      win._page_keys[win.stack.currentIndex()] == "customize")
check("it stays open while its page is", nav.is_open())
check("the sub entry is marked", nav.button.isChecked())

parent_index = win._page_keys.index("settings")
parent = win.nav_group.button(parent_index)
check("and Settings stays marked as the section you are in",
      parent.isChecked())

print("\n  leaving settings rolls it back up:")
win.show_panel("gallery")
pump(0.6)
check("it closed", not nav.is_open())
check("and is no longer marked", not nav.button.isChecked())

print("\n  a colour change repaints the window that is already open:")
win.show_panel("customize")
pump(0.3)
sheet_before = app.styleSheet()
win.customize._use_preset("#2BB6C4", "#7C5CE0")
pump(0.3)
check("the application stylesheet was replaced",
      app.styleSheet() != sheet_before)
check("and carries the new colour", "#2BB6C4" in app.styleSheet())
win.customize._use_preset(theme.DEFAULT_MAIN, theme.DEFAULT_ACCENT)
pump(0.2)

eng.shutdown()



print("\n=== style presets ===")
from avcore.styles import BUILT_IN, StylePresets
from avgui.dialogs import StyleEditor

sty = tmp / "styles"
sty.mkdir(exist_ok=True)
presets = StylePresets(sty / "styles.json")

check("the built-ins are there", len(presets.all()) == len(BUILT_IN),
      f"{len(presets.all())}")
check("they are marked as built in",
      all(p["built_in"] for p in presets.all()))
check("a built-in carries a suffix",
      presets.suffix_for("Detailed") == "highly detailed, dramatic lighting")
check("the 'None' style adds nothing", presets.suffix_for("None") == "")

print("\n  making one:")
ok, name = presets.save("Oil painting", "thick oil paint, warm light")
check("it saved", ok, str(name))
check("it appears alongside the built-ins",
      len(presets.all()) == len(BUILT_IN) + 1)
check("it is not marked built in",
      not presets.is_built_in("Oil painting"))
check("its suffix comes back",
      presets.suffix_for("Oil painting") == "thick oil paint, warm light")
check("it survives a reload",
      StylePresets(sty / "styles.json").find("Oil painting") is not None)

print("\n  names are checked:")
check("an empty name is refused",
      presets.validate("") == "A name is needed.")
check("a duplicate is refused",
      "already exists" in presets.validate("Oil painting"))
check("a built-in name is refused",
      "built-in" in presets.validate("Detailed"))
check("its own name is fine when editing",
      presets.validate("Oil painting", original="Oil painting") == "")
check("a new name is fine", presets.validate("Charcoal") == "")

print("\n  editing keeps one entry, not two:")
ok, _ = presets.save("Oil paint", "thick oil paint, cool light",
                     original="Oil painting")
check("the edit saved", ok)
check("still only one custom style", len(presets.custom()) == 1,
      str([p["name"] for p in presets.custom()]))
check("under the new name", presets.find("Oil paint") is not None)
check("and the old name is gone", presets.find("Oil painting") is None)

print("\n  built-ins cannot be removed:")
check("removing a built-in is refused", not presets.remove("Detailed"))
check("it is still there", presets.find("Detailed") is not None)
check("a custom one can be removed", presets.remove("Oil paint"))
check("and it is gone", presets.find("Oil paint") is None)

print("\n  the editor dialog:")
dlg = StyleEditor(presets)
check("Save is disabled until it has a name", not dlg.ok.isEnabled())
dlg.name_box.setText("Neon noir")
check("naming it enables Save", dlg.ok.isEnabled())
dlg.name_box.setText("Detailed")
check("a built-in name disables Save again", not dlg.ok.isEnabled())
check("and says why", not dlg.error.isHidden())
dlg.name_box.setText("Neon noir")
dlg.suffix_box.setPlainText("neon signs, wet asphalt")
check("the preview shows what a prompt becomes",
      dlg.preview.text().endswith("neon signs, wet asphalt"),
      dlg.preview.text()[-40:])
dlg._accept()
check("saving added it", presets.find("Neon noir") is not None)

print("\n  choosing one in the panel sets the suffix used everywhere:")
sc = Settings.load(path=sty / "s.json")
sc.save()
engc = Engine(sc)
engc.styles = presets
pc = CustomizePanel(engc)
pc._reload_styles(select="Neon noir")
check("the panel selected it", pc._selected_style() == "Neon noir")
check("the suffix reached the settings",
      sc.get("image.style_suffix") == "neon signs, wet asphalt",
      sc.get("image.style_suffix"))
check("and the name was remembered",
      sc.get("image.style_name") == "Neon noir")
check("editing is offered for a custom style", pc.edit_btn.isEnabled())

pc._reload_styles(select="Detailed")
check("a built-in cannot be edited", not pc.edit_btn.isEnabled())
check("nor deleted", not pc.delete_btn.isEnabled())
check("but it still applies its suffix",
      sc.get("image.style_suffix") == "highly detailed, dramatic lighting")
engc.shutdown()

print("\n=== the two style pickers stay in step ===")
# One list lives in the engine and both panels draw from it. Anything
# else means two places that can disagree about which style is in use.
from avgui.prompt_panel import PromptPanel

sync = tmp / "sync"
sync.mkdir(exist_ok=True)
ss = Settings.load(path=sync / "s.json")
ss.save()
engs = Engine(ss)
engs.styles = StylePresets(sync / "styles.json")
engs._set(styles=engs.styles.all(),
          style_name=ss.get("image.style_name"))

cust = CustomizePanel(engs)
prompt = PromptPanel(engs)


def fan():
    snap = engs.snapshot()
    cust.on_state(snap)
    prompt.on_state(snap)


fan()
check("both start with the built-ins",
      prompt.style_pick.count() == cust.style_list.count(),
      f"{prompt.style_pick.count()} vs {cust.style_list.count()}")

print("\n  a style made in Customize appears in the prompt picker:")
engs.styles.save("Oil painting", "thick oil paint")
engs.refresh_styles()
fan()
check("the picker grew", prompt.style_pick.count() == 3,
      str(prompt.style_pick.count()))
check("it is selectable there",
      prompt.style_pick.findData("Oil painting") >= 0)

print("\n  choosing in the prompt picker updates Customize:")
prompt.style_pick.setCurrentIndex(
    prompt.style_pick.findData("Oil painting"))
fan()
check("the engine knows", engs.snapshot()["style_name"] == "Oil painting")
check("Customize moved its selection too",
      cust._selected_style() == "Oil painting", str(cust._selected_style()))
check("and the suffix is what prompts will get",
      ss.get("image.style_suffix") == "thick oil paint")

print("\n  editing the text updates what is applied, name unchanged:")
engs.styles.save("Oil painting", "thick oil paint, cool light",
                 original="Oil painting")
engs.refresh_styles()
fan()
check("the settings suffix followed",
      ss.get("image.style_suffix") == "thick oil paint, cool light",
      ss.get("image.style_suffix"))
check("and so did the tooltip beside the tick",
      "cool light" in prompt.use_suffix.toolTip(),
      prompt.use_suffix.toolTip())

print("\n  deleting the style in use clears the selection:")
engs.styles.remove("Oil painting")
engs.refresh_styles()
fan()
check("nothing is selected now", engs.snapshot()["style_name"] == "")
check("the picker shrank", prompt.style_pick.count() == 2)
check("and Customize agrees", cust.style_list.count() == 2)

print("\n  a fresh panel shows the same thing:")
rebuilt = PromptPanel(engs)
rebuilt.on_state(engs.snapshot())
check("same number of entries",
      rebuilt.style_pick.count() == prompt.style_pick.count())
engs.shutdown()




print("\n=== decorative themes ===")
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QRegion
from demo_stubs import write_png
from avgui.decorations import DecorationLayer
from avgui.sounds import EFFECTS, VOLUME, ThemeSounds, ensure_sounds
from avgui.themes import painter_for, theme_names

dec = tmp / "decor"
(dec / "out").mkdir(parents=True, exist_ok=True)
sd = Settings.load(path=dec / "s.json")
sd.set("paths.overlay_dir", str(dec / "out"))
sd.set("ui.decor_theme", "none")
sd.save()
engd = Engine(sd)
wind = MainWindow(engd)
wind.resize(1000, 700)
wind.show()
pump(0.5)

check("there is a decoration layer",
      isinstance(wind.decor, DecorationLayer))
check("nothing is decorated by default", not wind.decor.isVisible(),
      "decor_theme starts as none")
check("themes are listed", len(theme_names()) >= 2)
check("'none' has no painter", painter_for("none") is None)
check("halloween has one", painter_for("halloween") is not None)

print("\n  turning a theme on:")
wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("halloween"))
pump(0.4)
check("the layer appears", wind.decor.isVisible())
check("the setting was saved", sd.get("ui.decor_theme") == "halloween")
check("it is transparent to the mouse",
      wind.decor.testAttribute(Qt.WA_TransparentForMouseEvents),
      "or it would swallow every click")

print("\n  the Fancy toggle drives the animation:")
wind.customize.fancy.setChecked(True)
pump(0.2)
check("animating", wind.decor._frames.isActive())
wind.customize.fancy.setChecked(False)
pump(0.2)
check("stopped", not wind.decor._frames.isActive())
check("but the decoration is still drawn", wind.decor.isVisible(),
      "Fancy governs motion, not decoration")
wind.customize.fancy.setChecked(True)

print("\n  artwork is never covered:")
art = dec / "out" / "art.png"
write_png(art, 640, 360, (120, 90, 60))
engd.publish_overlay(art, source="live")
wind.show_panel("live")
pump(0.6)
wind.decor._remeasure()
rect = wind.decor._artwork_rect(wind.live.preview)
check("the artwork rectangle was found", rect is not None, str(rect))
if rect is not None:
    middle = QPoint(rect.center())
    check("the patterned region excludes it",
          not wind.decor._clip.contains(middle))
    check("and so does the region used for the tint",
          not wind.decor._safe.contains(middle),
          "a wash over a picture is still covering it")

print("\n  controls and text are excluded from patterns only:")
btn = wind.live.listen_btn
at = QPoint(btn.mapTo(wind, QPoint(0, 0)))
at += QPoint(btn.width() // 2, btn.height() // 2)
check("a button is outside the patterned region",
      not wind.decor._clip.contains(at))
check("but inside the placed region",
      wind.decor._safe.contains(at),
      "a candle glow near a button is fine")

print("\n  dragging a splitter re-measures:")
before = QRegion(wind.decor._clip)
wind.live.split.setSizes([260, 300])
pump(0.5)
wind.decor._remeasure()
check("the region changed with the layout",
      wind.decor._clip != before)

print("\n=== spooky sounds ===")
check("silent by default", sd.get("ui.decor_sounds") is False,
      "opt-in, as agreed")
check("the player starts disabled", not wind.sounds.enabled)

print("\n  every effect can be generated:")
made = ensure_sounds(dec / "sounds")
check("all six exist", len(made) == len(EFFECTS), f"{len(made)}")
check("they are real audio files",
      all(p.stat().st_size > 2000 for p in made.values()))
check("each has a volume", all(name in VOLUME for name in EFFECTS))
# Checked per theme now that each has its own set: whatever a theme
# plays on every page change must be the quietest thing it owns.
from avgui.sounds import EVENTS as _EVENTS
for theme_name, mapping in _EVENTS.items():
    used = {VOLUME[v] for v in mapping.values()}
    check(f"{theme_name}: its page sound is its quietest",
          VOLUME[mapping["page"]] == min(used),
          f"{mapping['page']} at {VOLUME[mapping['page']]}")

print("\n  turning them on:")
wind.customize.spooky.setChecked(True)
pump(0.3)
check("the setting saved", sd.get("ui.decor_sounds") is True)
check("the player is enabled", wind.sounds.enabled)

print("\n  they follow transitions, not values:")


class Counter:
    """Stands in for the player, counting what would be heard."""

    def __init__(self):
        self.enabled = True
        self.theme = "halloween"
        self.played = []

    def set_theme(self, name):
        self.theme = name

    def play(self, name):
        self.played.append(name)
        return True

    def play_event(self, event):
        """Resolved the same way the real player does, so the test sees
        the effect a theme would actually reach for."""
        if not self.enabled:
            return False
        from avgui.sounds import EVENTS
        name = EVENTS.get(self.theme, {}).get(event)
        if name is None:
            return False
        return self.play(name)


counter = Counter()
wind.sounds = counter
wind._heard_state = {"listening": False, "generated": 0,
                     "overlay_cleared": False}

wind._sound_for({"listening": True, "generated": 0,
                 "overlay_cleared": False})
check("starting to listen speaks once", counter.played == ["creak"],
      str(counter.played))
counter.played.clear()
for _ in range(5):
    wind._sound_for({"listening": True, "generated": 0,
                     "overlay_cleared": False})
check("staying in that state says nothing more", counter.played == [],
      "otherwise it would chatter for as long as it listened")

wind._sound_for({"listening": False, "generated": 0,
                 "overlay_cleared": False})
check("stopping has its own sound", counter.played == ["latch"],
      str(counter.played))
counter.played.clear()

wind._sound_for({"listening": False, "generated": 0,
                 "overlay_cleared": True})
check("clearing the overlay whooshes", counter.played == ["whoosh"],
      str(counter.played))
counter.played.clear()

wind._sound_for({"listening": False, "generated": 1,
                 "overlay_cleared": True})
check("a finished image chimes", counter.played == ["chime"],
      str(counter.played))
counter.played.clear()

print("\n  changing page rustles:")
wind.show_panel("gallery")
check("it rustled", "rustle" in counter.played, str(counter.played))
counter.played.clear()
wind.show_panel("gallery")
check("going nowhere is silent", counter.played == [],
      "the same page twice is not a change")

print("\n  the logo easter egg:")
counter.played.clear()


class Press:
    def button(self):
        return Qt.LeftButton


wind._logo_pressed(Press())
check("clicking the mark plays its own sound",
      counter.played == ["wisp"], str(counter.played))

print("\n  silence is honoured everywhere:")
counter.enabled = False
counter.played.clear()
wind.show_panel("live")
wind._sound_for({"listening": True, "generated": 9,
                 "overlay_cleared": True})
wind._logo_pressed(Press())
check("nothing plays when sounds are off", counter.played == [],
      str(counter.played))

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("none"))
pump(0.3)
check("no theme means no sound either",
      not wind.sounds.enabled if hasattr(wind.sounds, "enabled") else True,
      "sound belongs to the theme")

print("\n  sound-player wiring is deterministic without audio hardware:")
# GitHub's Windows runner has Qt Multimedia installed but no audio endpoint.
# In that environment QSoundEffect exists yet remains in Loading forever,
# so asserting Ready/isPlaying would test runner hardware rather than our
# wiring. Replace only the Qt effect with a stateful stand-in; ThemeSounds
# still creates every player, assigns sources/volumes, and calls play().
import PySide6.QtMultimedia as _qt_multimedia

_RealSoundEffect = _qt_multimedia.QSoundEffect


class _FakeSoundEffect:
    class Status:
        Loading = 0
        Ready = 1

    def __init__(self, *_args, **_kwargs):
        self._source = None
        self._volume = 1.0
        self._playing = False

    def setSource(self, source):
        self._source = source

    def source(self):
        return self._source

    def setVolume(self, value):
        self._volume = float(value)

    def volume(self):
        return self._volume

    def status(self):
        return self.Status.Ready

    def play(self):
        self._playing = True

    def stop(self):
        self._playing = False

    def isPlaying(self):
        return self._playing


_qt_multimedia.QSoundEffect = _FakeSoundEffect

real = ThemeSounds(dec / "real_sounds")
real.set_enabled(True)
check("every effect loaded", len(real._players) == len(EFFECTS),
      f"{len(real._players)} of {len(EFFECTS)}")
check("all of them reached Ready",
      all(effect.status() == effect.Status.Ready
          for effect in real._players.values()),
      f"{len(real._players)} of {len(EFFECTS)}")
check("volumes came from the table",
      all(abs(real._players[n].volume() - VOLUME[n]) < 0.01
          for n in real._players))
check("playing one is accepted", real.play("wisp"))
check("the selected effect enters playing state",
      real._players["wisp"].isPlaying())
real.set_enabled(False)
check("and refused once disabled", not real.play("wisp"))

print("\n  a theme may reshape controls, since it cannot paint on them:")
from avgui.themes import style_sheet

check("a plain theme adds no rules", style_sheet("none") == "")
sheet = style_sheet("halloween")
check("halloween brings its own", len(sheet) > 500, f"{len(sheet)} chars")
check("the braces balance", sheet.count("{") == sheet.count("}"))
check("it is plain ascii", all(ord(c) < 128 for c in sheet),
      "a stray unicode character silently breaks a stylesheet")
check("buttons are reshaped", "border-top-left-radius" in sheet)
check("the primary button is styled", "#primaryButton" in sheet)
check("inputs are reshaped too, so the two agree",
      "QLineEdit" in sheet)
check("the confirmations keep their own colours",
      "background" not in sheet.split("#confirmButton")[1][:160],
      "green means applied whatever the theme")

print("\n  it reaches the running application:")
# Set explicitly: an earlier section turned the theme off, and a test
# that silently depends on what came before it is worse than no test.
sd.set("ui.decor_theme", "halloween")
theme.apply_to(app, sd)
live_sheet = app.styleSheet()
check("the decorative rules are appended",
      "border-top-left-radius: 10px" in live_sheet)
check("the base rules are still there", "#navButton" in live_sheet)

sd.set("ui.decor_theme", "none")
theme.apply_to(app, sd)
check("turning the theme off removes them",
      "border-top-left-radius: 10px" not in app.styleSheet())
sd.set("ui.decor_theme", "halloween")
theme.apply_to(app, sd)

print("\n  the tally glow follows the lamp, not the theme:")
from avgui.themes import _tally_glow

captured = []


class Probe:
    """Records what the glow would draw."""

    def setPen(self, *_a):
        pass

    def setBrush(self, brush):
        captured.append(brush)

    def drawEllipse(self, *_a):
        pass


for state in ("idle", "live", "working"):
    captured.clear()
    wind.live.tally.set_state(state)
    _tally_glow(Probe(), wind, wind.live, 0.0)
    check(f"{state}: a glow was drawn", len(captured) == 1)

# The colour has to differ, or the decoration would be claiming the app
# is doing something it is not.
colours = []
for state in ("idle", "live", "working"):
    captured.clear()
    wind.live.tally.set_state(state)
    _tally_glow(Probe(), wind, wind.live, 0.0)
    colours.append(captured[0].stops()[0][1].name())
check("each state glows a different colour",
      len(set(colours)) == 3, str(colours))
check("live is the reddest",
      QColor(colours[1]).red() > QColor(colours[0]).red())
wind.live.tally.set_state("idle")

print("\n  a theme's options appear only when that theme is on:")
# Declared by the theme, not hard-coded here, so a future theme without
# sounds will never show a sound toggle.
from avgui.themes import options_for

check("a plain theme offers nothing", options_for("none") == ())
check("halloween offers animation and sounds",
      {"animate", "sounds"} <= set(options_for("halloween")),
      str(options_for("halloween")))

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("none"))
pump(0.3)
check("with no theme, Fancy is hidden", wind.customize.fancy.isHidden())
check("and so is Spooky sounds", wind.customize.spooky.isHidden())
check("and the note says nothing at all",
      wind.customize.decor_note.text() == "",
      repr(wind.customize.decor_note.text()))

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("halloween"))
pump(0.3)
check("choosing Halloween reveals Fancy",
      not wind.customize.fancy.isHidden())
check("and Spooky sounds", not wind.customize.spooky.isHidden())
check("hidden, not merely greyed out",
      wind.customize.fancy.isEnabled(),
      "a disabled control still asks to be understood")

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("none"))
pump(0.3)
check("turning the theme off hides them again",
      wind.customize.fancy.isHidden()
      and wind.customize.spooky.isHidden())

print("\n  the theme survives a layout switch:")
# Switching layout builds a new central widget, which Qt stacks above
# the overlay, and destroys the panels the cached regions were measured
# from. Parts of the theme simply vanished until both were put right.
sd.set("ui.decor_theme", "halloween")
wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("halloween"))
pump(0.4)


def decor_on_top():
    kids = [c for c in wind.children()
            if hasattr(c, "isWidgetType") and c.isWidgetType()]
    central = wind.centralWidget()
    if wind.decor not in kids or central not in kids:
        return False
    return kids.index(wind.decor) > kids.index(central)


check("the layer is above the interface to begin with", decor_on_top())

for layout in ("sidebar", "split", "hybrid", "split", "sidebar", "hybrid"):
    wind.set_layout(layout, save=False)
    pump(0.35)
    check(f"{layout}: still drawn", wind.decor.isVisible())
    check(f"{layout}: still on top", decor_on_top())
    check(f"{layout}: geometry was re-measured",
          wind.decor._clip is not None
          and wind.decor._clip.rectCount() > 0,
          f"{wind.decor._clip.rectCount() if wind.decor._clip else 0} pieces")
    check(f"{layout}: it fills the window",
          wind.decor.size() == wind.size(),
          f"{wind.decor.width()}x{wind.decor.height()}")

engd.shutdown()

shutil.rmtree(tmp, ignore_errors=True)

print("\n=== the starfield theme ===")
from avgui.sounds import EVENTS
from avgui.themes import OPTIONS, SHEETS, option_label, painter_for

check("it is offered", "starfield" in
      [k for k, _l, _n in theme_names()])
check("it has a painter", painter_for("starfield") is not None)
check("it declares its options",
      {"animate", "sounds"} <= set(options_for("starfield")))
check("it brings its own stylesheet",
      len(style_sheet("starfield")) > 500)
check("that stylesheet is ascii",
      all(ord(c) < 128 for c in style_sheet("starfield")),
      "a stray character silently breaks a sheet")
check("and its braces balance",
      style_sheet("starfield").count("{")
      == style_sheet("starfield").count("}"))

print("\n  every theme is registered consistently:")
# A theme half-registered - painter but no options, or a sheet with no
# theme entry - fails in a way nobody notices until it is selected.
named = {k for k, _l, _n in theme_names() if k != "none"}
check("each has a painter",
      all(painter_for(k) is not None for k in named), str(named))
check("each declares options", all(k in OPTIONS for k in named))
check("each has a stylesheet", all(k in SHEETS for k in named))
check("each has a sound set", all(k in EVENTS for k in named))
check("no stylesheet is registered for a theme that does not exist",
      set(SHEETS) <= named, str(set(SHEETS) - named))

print("\n  options are named by the theme, not by the panel:")
check("halloween calls them Spooky sounds",
      option_label("halloween", "sounds") == "Spooky sounds")
check("starfield does not",
      option_label("starfield", "sounds") == "Cosmic sounds",
      option_label("starfield", "sounds"))
check("an unknown theme still gets a sensible name",
      option_label("nonesuch", "sounds") == "Theme sounds")

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("starfield"))
pump(0.4)
check("the panel relabels the toggle",
      wind.customize.spooky.text() == "Cosmic sounds",
      wind.customize.spooky.text())
check("the starfield is drawn", wind.decor.isVisible())

print("\n  sounds are chosen by event, not by name:")
check("both themes answer the same events",
      set(EVENTS["halloween"]) == set(EVENTS["starfield"]))
check("but with different effects",
      EVENTS["halloween"]["listen_start"]
      != EVENTS["starfield"]["listen_start"],
      "a door creak in a starfield would be absurd")
check("every mapped effect exists",
      all(v in EFFECTS for m in EVENTS.values() for v in m.values()))

sounds = ThemeSounds(dec / "event_sounds")
sounds.set_enabled(True)
sounds.set_theme("starfield")
if not sounds.error:
    check("an event plays under starfield",
          sounds.play_event("listen_start"))
    sounds.set_theme("none")
    check("a theme with no sound set stays quiet",
          not sounds.play_event("listen_start"),
          "silence beats the wrong sound")
    sounds.set_enabled(False)

print("\n=== the underwater theme ===")
check("it is offered",
      "underwater" in [k for k, _l, _n in theme_names()])
check("it has a painter", painter_for("underwater") is not None)
check("it declares its options",
      {"animate", "sounds"} <= set(options_for("underwater")))
check("its sounds are its own",
      EVENTS["underwater"]["listen_start"] == "surge",
      "a door creak or a bell would both be wrong down there")
check("its option is named for the theme",
      option_label("underwater", "sounds") == "Watery sounds",
      option_label("underwater", "sounds"))

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("underwater"))
pump(0.4)
check("the panel switched to it", wind.decor.isVisible())
check("and relabelled the toggle",
      wind.customize.spooky.text() == "Watery sounds",
      wind.customize.spooky.text())

print("\n  animation drives the living things:")
# Everything that moves reads a phase, so turning Fancy off leaves them
# still rather than needing a separate frozen painter.
wind.customize.fancy.setChecked(False)
pump(0.25)
check("the timer stops", not wind.decor._frames.isActive())
check("but the water is still drawn", wind.decor.isVisible())
wind.customize.fancy.setChecked(True)
pump(0.25)
check("and starts again", wind.decor._frames.isActive())

print("\n=== the kawaii theme ===")
check("it is offered",
      "kawaii" in [k for k, _l, _n in theme_names()])
check("it has a painter", painter_for("kawaii") is not None)
check("its sounds are its own",
      EVENTS["kawaii"]["listen_start"] == "twinkle")
check("its option is named for the theme",
      option_label("kawaii", "sounds") == "Cute sounds",
      option_label("kawaii", "sounds"))

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("kawaii"))
pump(0.4)
check("the panel switched to it", wind.decor.isVisible())
check("and relabelled the toggle",
      wind.customize.spooky.text() == "Cute sounds",
      wind.customize.spooky.text())

print("\n  a renamed theme still resolves from saved settings:")
# Someone who chose it under the old name keeps their theme instead of
# silently dropping back to no decoration.
from avgui.themes import ALIASES, canonical

check("the old name maps to the new one",
      canonical("sanrio") == "kawaii", canonical("sanrio"))
check("its painter is found by the old name",
      painter_for("sanrio") is not None)
check("so is its stylesheet", len(style_sheet("sanrio")) > 500)
check("and its options",
      {"animate", "sounds"} <= set(options_for("sanrio")),
      "the old name still resolves to a theme with options")
check("and its label", option_label("sanrio", "sounds") == "Cute sounds")
check("an unknown name is left alone", canonical("nonesuch") == "nonesuch")
check("every alias points at a real theme",
      all(v in [k for k, _l, _n in theme_names()]
          for v in ALIASES.values()), str(ALIASES))

print("\n=== the cryptic theme ===")
check("it is offered",
      "cryptic" in [k for k, _l, _n in theme_names()])
check("it has a painter", painter_for("cryptic") is not None)
check("its sounds are its own",
      EVENTS["cryptic"]["listen_start"] == "drone")
check("its option is named for the theme",
      option_label("cryptic", "sounds") == "Arcane sounds",
      option_label("cryptic", "sounds"))

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("cryptic"))
pump(0.4)
check("the panel switched to it", wind.decor.isVisible())
check("and relabelled the toggle",
      wind.customize.spooky.text() == "Arcane sounds",
      wind.customize.spooky.text())

print("\n  the eye follows the cursor:")
# The iris is clamped inside the lens, so wherever it is asked to look
# it stays part of the eye rather than sliding out of it.
from avgui.themes import _eye
from PySide6.QtCore import QPointF as _QPointF
from PySide6.QtGui import QImage as _QImage, QPainter as _QP


def iris_offset(look_at):
    """Where the iris lands, read back from what was drawn."""
    seen = []

    class Probe:
        def __getattr__(self, _name):
            return lambda *a, **k: None

        def drawEllipse(self, centre, rx, ry):
            seen.append((centre.x(), centre.y(), rx))

        def save(self):
            pass

        def restore(self):
            pass

    _eye(Probe(), 200.0, 100.0, 80.0, 0.0, look_at=look_at)
    # The iris is the largest ellipse drawn inside the lens.
    return max(seen, key=lambda e: e[2]) if seen else None


left = iris_offset(_QPointF(0.0, 100.0))
right = iris_offset(_QPointF(400.0, 100.0))
up = iris_offset(_QPointF(200.0, 0.0))
down = iris_offset(_QPointF(200.0, 200.0))

check("looking left moves the iris left", left[0] < 200.0, f"{left[0]:.1f}")
check("looking right moves it right", right[0] > 200.0, f"{right[0]:.1f}")
check("it tracks vertically too", up[1] < 100.0 < down[1],
      f"{up[1]:.1f} vs {down[1]:.1f}")
check("the iris stays inside the eye",
      abs(left[0] - 200.0) < 80.0 * 0.3
      and abs(right[0] - 200.0) < 80.0 * 0.3,
      "clamped to an ellipse within the lens")

centred = iris_offset(_QPointF(200.0, 100.0))
check("a cursor on the eye itself does not jitter",
      abs(centred[0] - 200.0) < 1.0 and abs(centred[1] - 100.0) < 1.0)

drifting = iris_offset(None)
check("with no cursor it drifts on its own", drifting is not None,
      "the eye keeps moving when the pointer leaves the window")

print("\n=== the frutiger aero theme ===")
check("it is offered", "aero" in [k for k, _l, _n in theme_names()])
check("it is named in full in the picker",
      any(label == "Frutiger Aero" for _k, label, _n in theme_names()),
      "the key is short; the label is what people read")
check("it has a painter", painter_for("aero") is not None)
check("its sounds are its own", EVENTS["aero"]["listen_start"] == "gloss")
check("its option is named for the theme",
      option_label("aero", "sounds") == "Glassy sounds",
      option_label("aero", "sounds"))

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("aero"))
pump(0.4)
check("the panel switched to it", wind.decor.isVisible())
check("and relabelled the toggle",
      wind.customize.spooky.text() == "Glassy sounds",
      wind.customize.spooky.text())

print("\n  every stylesheet stays plain ascii:")
# A stray non-ascii character in a colour makes Qt drop the rule
# silently. It has slipped in twice while writing themes.
for key, _label, _note in theme_names():
    sheet = style_sheet(key)
    check(f"{key}: ascii only",
          all(ord(c) < 128 for c in sheet),
          f"{len(sheet)} chars")

print("\n=== the desert theme ===")
check("it is offered", "desert" in [k for k, _l, _n in theme_names()])
check("it has a painter", painter_for("desert") is not None)
check("its sounds are its own",
      EVENTS["desert"]["listen_start"] == "gust")
check("its option is named for the theme",
      option_label("desert", "sounds") == "Desert sounds",
      option_label("desert", "sounds"))
check("it offers the enhance toggle like the rest",
      "enhance" in options_for("desert"))

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("desert"))
pump(0.4)
check("the panel switched to it", wind.decor.isVisible())
check("and relabelled the toggle",
      wind.customize.spooky.text() == "Desert sounds",
      wind.customize.spooky.text())

print("\n  its page-change sound is the quietest it owns:")
# Checked per theme: whatever plays on every page change has to be the
# least obtrusive thing in the set.
used = {VOLUME[v] for v in EVENTS["desert"].values()}
check("grit is the quietest",
      VOLUME[EVENTS["desert"]["page"]] == min(used),
      f"{EVENTS['desert']['page']} at {VOLUME[EVENTS['desert']['page']]}")

import avgui.themes as themes_module

print("\n=== the arctic theme ===")
check("it is offered", "arctic" in [k for k, _l, _n in theme_names()])
check("it has a painter", painter_for("arctic") is not None)
check("its sounds are its own",
      EVENTS["arctic"]["listen_start"] == "frostfall")
check("its option is named for the theme",
      option_label("arctic", "sounds") == "Icy sounds",
      option_label("arctic", "sounds"))

# It withheld the enhance toggle until its enhanced artwork existed -
# an option that does nothing is worse than no option - and offers it
# now that the artwork is there.
check("it offers the enhance toggle now",
      "enhance" in options_for("arctic"),
      str(options_for("arctic")))
check("along with animation and sounds",
      {"animate", "sounds"} <= set(options_for("arctic")))
check("and it brings an aurora, enhanced only",
      hasattr(themes_module, "_aurora"),
      "an addition rather than a better version of something")

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("arctic"))
pump(0.4)
check("the panel switched to it", wind.decor.isVisible())
check("and shows all three options",
      not wind.customize.enhance.isHidden()
      and not wind.customize.fancy.isHidden()
      and not wind.customize.spooky.isHidden())

print("\n  offering the toggle and having artwork must not drift apart:")
# Rendered both ways and compared: a theme that offers the option must
# actually draw differently, and one that withholds it must not. An
# assertion like "offers in (True, False)" would pass forever.
from PySide6.QtGui import QImage as _QImg, QPainter as _QPtr


def _draws_differently(key):
    fn = painter_for(key)
    frames = []
    for rich in (False, True):
        image = _QImg(420, 300, _QImg.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = _QPtr(image)
        fn(painter, 420, 300, wind, QRegion(0, 0, 420, 300),
           QRegion(0, 0, 420, 300), 0.0, rich)
        painter.end()
        frames.append(image)
    differing = sum(
        1 for y in range(0, 300, 4) for x in range(0, 420, 4)
        if frames[0].pixel(x, y) != frames[1].pixel(x, y))
    return differing > 0


for key, _label, _note in theme_names():
    if key == "none":
        continue
    offers = "enhance" in options_for(key)
    differs = _draws_differently(key)
    check(f"{key}: {'offers and differs' if offers else 'withholds'}",
          offers == differs,
          f"offered={offers} differs={differs}")

print("\n  both sets run at the same pace:")
# The enhanced artwork was briefly slowed, back when the underwater
# shafts cost 57ms a frame. Buffering halved that, and the slower rate
# read as lag in its own right - fewer frames of a moving thing looks
# like the app struggling whatever the thread is doing.
from avgui.decorations import FRAME_MS, RICH_FRAME_MS

check("enhanced is not paced slower", RICH_FRAME_MS == FRAME_MS,
      f"{FRAME_MS}ms vs {RICH_FRAME_MS}ms")
wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("underwater"))
wind.customize.enhance.setChecked(False)
pump(0.4)
check("plain runs at the full rate",
      wind.decor._frames.interval() == FRAME_MS,
      str(wind.decor._frames.interval()))
wind.customize.enhance.setChecked(True)
pump(0.4)
check("turning it on keeps the full rate",
      wind.decor._frames.interval() == RICH_FRAME_MS,
      "the pace still follows the artwork, should it ever differ again")
before = wind.decor._phase
pump(0.6)
check("but the animation still advances with real time",
      wind.decor._phase > before,
      "phase follows the clock, not the frame count")
wind.customize.enhance.setChecked(False)
pump(0.3)
check("and turning it off restores the rate",
      wind.decor._frames.interval() == FRAME_MS)

print("\n  each theme paints without complaint:")
# Every painter is called against a real window, at two phases, so a
# crash in one is caught here rather than when someone selects it.
from PySide6.QtGui import QImage, QPainter as _QPainter

for key, _label, _note in theme_names():
    fn = painter_for(key)
    if fn is None:
        continue
    ok = True
    for ph in (0.0, 7.3):
        image = QImage(640, 480, QImage.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = _QPainter(image)
        try:
            fn(painter, 640, 480, wind,
               QRegion(0, 0, 640, 480), QRegion(0, 0, 640, 480), ph)
        except Exception as exc:
            ok = False
            print(f"     {key} raised: {type(exc).__name__}: {exc}")
        finally:
            painter.end()
    check(f"{key}: paints at rest and mid-animation", ok)

print("\n=== enhanced visuals ===")
# A third option per theme, opt-in like sounds. Both sets of artwork are
# kept: the plain one is lighter to draw and some people prefer it, so
# this is a preference rather than a fix.
# Not every theme: one that has no enhanced artwork withholds the
# toggle rather than showing it doing nothing. What must hold is that
# most do, and that the ones offering it really differ - checked by
# rendering, further down.
offering = [k for k, _l, _n in theme_names()
            if k != "none" and "enhance" in options_for(k)]
check("most themes offer it", len(offering) >= 5, str(offering))
check("it has a default name",
      option_label("nonesuch", "enhance") == "Enhance visuals",
      option_label("nonesuch", "enhance"))
check("it is off by default", sd.get("ui.decor_enhanced") is False,
      "the original art is what people see unless they ask")

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("underwater"))
pump(0.4)
check("the toggle is shown with a theme",
      not wind.customize.enhance.isHidden())
check("and unchecked", not wind.customize.enhance.isChecked())
check("the layer is drawing the plain art", not wind.decor._rich)

wind.customize.enhance.setChecked(True)
pump(0.4)
check("turning it on reaches the layer", wind.decor._rich)
check("and is saved", sd.get("ui.decor_enhanced") is True)

wind.customize.enhance.setChecked(False)
pump(0.3)
check("turning it off goes back", not wind.decor._rich)

wind.customize.decor_pick.setCurrentIndex(
    wind.customize.decor_pick.findData("none"))
pump(0.3)
check("it hides with no theme, like the others",
      wind.customize.enhance.isHidden())

print("\n  both sets of artwork paint cleanly:")
# Each painter is called twice over, plain and enhanced, so a fault in
# either set is caught here rather than when someone toggles it.
from PySide6.QtGui import QImage as _QI, QPainter as _QPtr

for key, _label, _note in theme_names():
    fn = painter_for(key)
    if fn is None:
        continue
    for rich in (False, True):
        ok = True
        for ph in (0.0, 5.7):
            image = _QI(640, 480, _QI.Format_ARGB32)
            image.fill(QColor(0, 0, 0, 0))
            painter = _QPtr(image)
            try:
                fn(painter, 640, 480, wind, QRegion(0, 0, 640, 480),
                   QRegion(0, 0, 640, 480), ph, rich)
            except Exception as exc:
                ok = False
                print(f"     {key} rich={rich}: "
                      f"{type(exc).__name__}: {exc}")
            finally:
                painter.end()
        check(f"{key}: {'enhanced' if rich else 'plain'} paints", ok)

print("\n=== the version number does something if you poke it ===")
# Twelve seconds of somebody shouting. Half a dozen overlapping copies
# is unpleasant rather than funny, so a click while it is playing does
# nothing at all - not queued, not restarted.
from avgui.assets import asset
from avgui.version_label import VersionLabel

clip = asset("well_do_it_live.wav")
check("the clip is in the assets folder", clip.exists(), str(clip.name))

egg = VersionLabel("v0.0.0")
check("it shows the version like any label", egg.text() == "v0.0.0")
check("and invites a click", egg.cursor().shape() == Qt.PointingHandCursor)
check("nothing is playing to begin with", not egg.is_playing())

started = egg.play()
check("clicking starts it", started)
pump(0.5)
check("it really is playing", egg.is_playing())

check("clicking again while it plays does nothing", egg.play() is False,
      "not queued, not restarted - the surprise is the joke")
check("and it is still the first one playing", egg.is_playing())

print("\n  it frees up once it has finished:")
# Rather than sit through twelve seconds, the player is stopped, which
# is the same state the clip ending leaves behind.
egg._effect.stop()
pump(0.3)
check("nothing is playing now", not egg.is_playing())
check("so it can be set off again", egg.play() is True)
egg._effect.stop()

print("\n  a missing clip costs a joke, not a crash:")
quiet = VersionLabel("v0.0.0")
quiet._failed = True
check("it simply declines", quiet.play() is False)
check("and asking again is still safe", quiet.play() is False)

egg.deleteLater()
quiet.deleteLater()

# Restore Qt Multimedia after the deterministic sound-state checks.
_qt_multimedia.QSoundEffect = _RealSoundEffect

print("\n=== keyboard shortcuts ===")


def _editable_combo():
    from PySide6.QtWidgets import QComboBox

    box = QComboBox()
    box.setEditable(True)
    return box

# Single keys on the two working pages. The awkward requirements are
# the ones worth pinning: they must not fire while somebody is typing,
# and they must not fire anywhere else in the app.
from PySide6.QtCore import QEvent
from PySide6.QtGui import QKeyEvent

from avgui.hotkeys import ACTIONS, DEFAULTS, describes_typing, normalise

check("every action has a default", len(DEFAULTS) == len(ACTIONS))
check("and they are all different",
      len(set(DEFAULTS.values())) == len(DEFAULTS),
      "two actions on one key means one of them stops working")

print("\n  keys are stored the way they will be compared:")
check("a bare letter survives", normalise("R") == "R")
check("lower case is lifted", normalise("r") == "R", normalise("r"))
check("space is named", normalise("Space") == "Space")
check("and nonsense becomes nothing", normalise("") == "",
      "a blank binding simply turns the shortcut off")

print("\n  what counts as typing:")
from PySide6.QtWidgets import (
    QComboBox, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox,
)

check("a line edit does", describes_typing(QLineEdit()))
check("a prompt box does", describes_typing(QPlainTextEdit()))
check("a number field does", describes_typing(QSpinBox()),
      "pressing X in a spin box is still typing")
check("an editable combo does",
      describes_typing(_editable_combo()))
check("a plain combo does not", not describes_typing(QComboBox()))
check("and a button does not", not describes_typing(QPushButton()))
check("nothing focused is not typing", not describes_typing(None))

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("the Customize sub-panel works")
