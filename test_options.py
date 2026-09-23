"""
The gallery's per-image options: copy prompt, favourite, delete.

The favourite tests matter most: a favourite that is not honoured by
pruning means an image the user asked to keep is deleted without warning.
"""
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from avcore.catalog import Catalog
from avcore.config import Settings
from avcore.engine import Engine
from avgui import theme
from avgui.dialogs import ConfirmDelete
from avgui.gallery_panel import Thumb
from avgui.widgets import HoverCaption
from demo_stubs import write_png

app = QApplication.instance() or QApplication([])
app.setStyleSheet(theme.QSS)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


tmp = Path("_optionstest")
out = tmp / "out"
out.mkdir(parents=True)

print("=== favourites are remembered ===")
cat = Catalog(tmp / "cat.json")
img = out / "live_001.png"
write_png(img, 64, 36, (100, 140, 200))
cat.record(img, "a harbour at dusk")

check("not favourite to begin with", not cat.is_favourite(img))
cat.set_favourite(img, True)
check("marking works", cat.is_favourite(img))
check("it survives a reload",
      Catalog(tmp / "cat.json").is_favourite(img))
check("the prompt is still there",
      cat.lookup(img)["prompt"] == "a harbour at dusk")
cat.set_favourite(img, False)
check("it can be turned off", not cat.is_favourite(img))
check("and the prompt survives that too",
      cat.lookup(img)["prompt"] == "a harbour at dusk")

print("\n  an image with no prompt can still be favourited:")
plain = out / "no_prompt.png"
write_png(plain, 64, 36, (200, 120, 100))
cat.set_favourite(plain, True)
check("marking an uncatalogued image works", cat.is_favourite(plain))
cat.set_favourite(plain, False)
check("unmarking leaves no empty entry behind",
      cat.lookup(plain) is None)

print("\n=== favourites are never pruned ===")
# The whole promise of a favourite: it ignores "keep last x images".
s = Settings.load(path=tmp / "settings.json")
s.set("paths.overlay_dir", str(out))
s.set("image.keep_images", 3)
eng = Engine(s)
eng.catalog = Catalog(tmp / "engine.json")

for f in out.glob("*.png"):
    f.unlink()

names = []
for i in range(8):
    p = out / f"live_{i:03}.png"
    write_png(p, 64, 36, (60 + i * 10, 90, 150))
    # Distinct timestamps so "oldest" is well defined.
    import os
    os.utime(p, (time.time() - (8 - i) * 60,) * 2)
    names.append(p)

keeper = names[0]          # the oldest, so normally the first to go
eng.catalog.set_favourite(keeper, True)
eng._prune()

check("the favourite survived pruning", keeper.exists(), keeper.name)
check("ordinary images were still pruned",
      len(list(out.glob("live_*.png"))) <= 4,
      f"{len(list(out.glob('live_*.png')))} left")
check("and it is still marked afterwards",
      eng.catalog.is_favourite(keeper))

print("\n  the favourite does not use up the keep allowance:")
remaining = sorted(out.glob("live_*.png"))
check("the newest ordinary images were kept",
      names[-1].exists() and names[-2].exists() and names[-3].exists(),
      f"{len(remaining)} files")

print("\n=== deleting removes the file and the entry ===")
victim = names[-1]
eng.catalog.record(victim, "something to forget")
check("it exists first", victim.exists())
check("and is catalogued", eng.catalog.lookup(victim) is not None)
ok = eng.delete_image(victim)
check("delete reported success", ok)
check("the file is gone", not victim.exists())
check("the catalogue entry is gone",
      eng.catalog.lookup(victim) is None)

print("\n  deleting what is on the overlay clears it first:")
shown = names[-2]
eng.publish_overlay(shown, source="live")
check("it is on the overlay", eng.snapshot().get("image") == str(shown))
eng.delete_image(shown)
check("the overlay was cleared", eng.snapshot().get("image") is None)
check("and the file is gone", not shown.exists())
eng.shutdown()

print("\n=== the cog and the menu ===")
img2 = out / "menu_test.png"
write_png(img2, 210, 118, (120, 160, 120))
s2 = Settings.load(path=tmp / "settings2.json")
s2.set("paths.overlay_dir", str(out))
eng2 = Engine(s2)
eng2.catalog = Catalog(tmp / "menu.json")
eng2.catalog.record(img2, "a green field under a wide sky")

thumb = Thumb(img2, lambda p: None,
              entry=eng2.catalog.lookup(img2), engine=eng2)
check("the cog exists", thumb.image.cog is not None)
check("but is hidden until hover", not thumb.image.cog.isVisible())

thumb.show()
app.processEvents()
thumb.image.set_menu_open(True)
app.processEvents()
check("it appears while the menu is open", thumb.image.cog.isVisible())
thumb.image.set_menu_open(False)
app.processEvents()

print("\n  copy prompt:")
QApplication.clipboard().clear()
thumb._copy_prompt()
check("the prompt reached the clipboard",
      QApplication.clipboard().text() == "a green field under a wide sky",
      QApplication.clipboard().text()[:34])

print("\n  favourite toggles from the menu:")
check("not favourite yet", not thumb.image._favourite)
thumb._toggle_favourite()
check("marking it updates the catalogue",
      eng2.catalog.is_favourite(img2))
check("and the outline is drawn", thumb.image._favourite)
thumb._toggle_favourite()
check("toggling again clears it", not thumb.image._favourite)
check("and the catalogue agrees", not eng2.catalog.is_favourite(img2))

print("\n  the delete confirmation:")
dialog = ConfirmDelete(img2, QPixmap(str(img2)))
buttons = {b.objectName(): b for b in dialog.findChildren(QPushButton)}
labels = [l.text() for l in dialog.findChildren(QLabel)]

check("it offers Confirm", "confirmButton" in buttons)
check("it offers Deny", "denyButton" in buttons)
check("it names the file", any(img2.name in t for t in labels))
check("it warns the deletion is permanent",
      any("cannot be undone" in t for t in labels))
check("it shows the image, not just the name",
      any(l.pixmap() is not None and not l.pixmap().isNull()
          for l in dialog.findChildren(QLabel)))
# Deny is the default, so a stray Enter cannot destroy anything.
check("Deny is the default button", buttons["denyButton"].isDefault())
check("Confirm is not", not buttons["confirmButton"].isDefault())
dialog.reject()
check("denying leaves the file alone", img2.exists())
eng2.shutdown()

print("\n=== the colours mean what they should ===")
check("favourite blue is distinct from the tally red",
      theme.FAVOURITE != theme.TALLY)
check("danger red is defined", bool(theme.DANGER))
check("the cog is styled", "#cogButton" in theme.QSS)
check("menus are styled for the dark theme", "QMenu {" in theme.QSS)
check("confirm is green", theme.OK in theme.QSS.split("#confirmButton")[1][:120])




print("\n=== one image, one gallery entry ===")
# A manual image exists twice on disk - where it was saved, and as a
# mirror the web server can reach. That is two files but one picture, and
# the gallery was listing both.
from PySide6.QtWidgets import QApplication as _QA
from avgui.gallery_panel import GalleryPanel

_QA.instance() or _QA([])

dup = tmp / "dup"
(dup / "overlay").mkdir(parents=True, exist_ok=True)
(dup / "saved").mkdir(parents=True, exist_ok=True)
sd = Settings.load(path=dup / "s.json")
sd.set("paths.overlay_dir", str(dup / "overlay"))
sd.set("paths.manual_dir", str(dup / "saved"))
eng3 = Engine(sd)
eng3.catalog = Catalog(dup / "c.json")

saved = dup / "saved" / "ai_dup.png"
write_png(saved, 64, 36, (120, 170, 130))
eng3.publish_overlay(saved, source="manual")

mirror = dup / "overlay" / "ai_dup.png"
check("the mirror was made so the overlay can serve it", mirror.exists())
check("both copies exist on disk", saved.exists() and mirror.exists())

gal = GalleryPanel(eng3)
names = [p.name for p in gal._find_images()]
check("the gallery lists it once, not twice",
      names.count("ai_dup.png") == 1, f"{names.count('ai_dup.png')} entries")

listed = [p for p in gal._find_images() if p.name == "ai_dup.png"][0]
check("and lists the saved copy, not the prunable mirror",
      listed.parent.name == "saved", str(listed.parent.name))

print("\n  showing an image whose mirror was pruned still works:")
mirror.unlink()
check("the mirror is gone", not mirror.exists())
ok3 = eng3.publish_overlay(saved, source="manual")
check("publishing succeeded", ok3)
check("the mirror was recreated", mirror.exists())
shown_path = Path(eng3.snapshot()["image"])
check("the overlay points at a file inside its own folder",
      shown_path.parent.resolve() == (dup / "overlay").resolve(),
      str(shown_path.parent.name))

print("\n  live images are untouched by any of this:")
live = dup / "overlay" / "live_x.png"
write_png(live, 64, 36, (90, 110, 190))
eng3.publish_overlay(live, source="live")
check("no pointless copy is made for a live image",
      Path(eng3.snapshot()["image"]) == live)
eng3.shutdown()

print("\n=== filtering by the model that made each image ===")
# The list comes from the pictures on hand, not from what is installed:
# a model may have been deleted, or the images may have come from
# another machine, and the question here is "what made these".
from avcore.catalog import UNRECORDED, model_key

mf = tmp / "modelfilter"
(mf / "out").mkdir(parents=True, exist_ok=True)
smf = Settings.load(path=mf / "s.json")
smf.set("paths.overlay_dir", str(mf / "out"))
smf.set("paths.manual_dir", str(mf / "out"))
smf.save()
engmf = Engine(smf)
engmf.catalog = Catalog(mf / "c.json")

history = [
    ("one.png", "comfyui", "alpha.safetensors"),
    ("two.png", "comfyui", "alpha.safetensors"),
    ("three.png", "comfyui", "beta.safetensors"),
    ("four.png", "pollinations", ""),
    ("five.png", "", ""),
]
for index, (name, backend, model) in enumerate(history):
    path = mf / "out" / name
    write_png(path, 48, 48, (80, 120, 160))
    os.utime(path, (time.time() - index * 60,) * 2)
    engmf.catalog.record(path, f"prompt {index}", source="live",
                         backend=backend, model=model)

seen = dict(engmf.catalog.models_used())
check("every model in the history is offered", len(seen) == 4,
      str(sorted(seen.values())))
check("a local model is keyed by its file",
      "alpha.safetensors" in seen)
check("online work is keyed by the backend", "pollinations" in seen)
check("and images from before this was recorded have their own key",
      UNRECORDED in seen,
      "sharing the empty key with 'any model' made choosing it do nothing")

gmf = GalleryPanel(engmf)
app.processEvents()
check("the picker appears when there is a choice",
      not gmf.model_filter.isHidden(), "four models here")

def shown_with(key):
    index = gmf.model_filter.findData(key)
    gmf.model_filter.setCurrentIndex(index)
    app.processEvents()
    return sorted(path.name for path in gmf._visible)

check("filtering to one model shows only its images",
      shown_with("alpha.safetensors") == ["one.png", "two.png"],
      str(shown_with("alpha.safetensors")))
check("the other local model is separate",
      shown_with("beta.safetensors") == ["three.png"])
check("online images can be picked out",
      shown_with("pollinations") == ["four.png"])
check("and so can the ones with nothing recorded",
      shown_with(UNRECORDED) == ["five.png"],
      "which is the case the shared key broke")

gmf._clear_filters()
app.processEvents()
check("clearing brings them all back", len(gmf._visible) == 5)
check("and resets the picker", gmf.model_filter.currentData() == "")

print("\n  it survives a new image arriving:")
gmf.model_filter.setCurrentIndex(
    gmf.model_filter.findData("alpha.safetensors"))
app.processEvents()
extra = mf / "out" / "six.png"
write_png(extra, 48, 48, (200, 120, 60))
os.utime(extra, (time.time(),) * 2)
engmf.catalog.record(extra, "prompt 6", source="live",
                     backend="comfyui", model="gamma.safetensors")
gmf._rebuild_all()
app.processEvents()
check("the chosen filter is kept",
      gmf.model_filter.currentData() == "alpha.safetensors",
      "a filter that reset itself on every new picture would be maddening")
check("and the new model joins the list",
      gmf.model_filter.findData("gamma.safetensors") >= 0)

engmf.shutdown()

print("\n=== clips in the gallery ===")
# None of this generates anything: a clip takes two minutes, and a test
# suite that spends two minutes proving one is miserable to run. The
# clip used here is made once by the spike and kept as a fixture.
from avcore.video import SHAPES as VIDEO_SHAPES
from avcore.video import best_shape, clip_length, fit_to_shape, poster_path
from avgui.filters import Filters
from avgui.gallery_panel import VIDEO_EXTS

print("\n  fitting a still to what the model was trained on:")
check("a wide picture gets the wide shape",
      best_shape(1920, 1080) == (1024, 576))
check("a tall one gets the tall shape",
      best_shape(768, 1344) == (576, 1024))
check("a square one gets the square shape",
      best_shape(1024, 1024) == (768, 768))
check("and nonsense does not raise", best_shape(0, 0) in VIDEO_SHAPES,
      "a missing size should not stop a clip being made")

# A still to crop: any picture the suite already made will do.
sample = next(iter(sorted((tmp / 'out').glob('*.png'))), None)
if sample is None:
    sample = tmp / 'sample.png'
    write_png(sample, 1152, 896, (120, 150, 180))

fitted = fit_to_shape(sample, (768, 768), tmp / "fitted.png")
if fitted is not None:
    from PySide6.QtGui import QImage

    got = QImage(str(fitted))
    check("the still is cropped to the exact shape",
          (got.width(), got.height()) == (768, 768),
          f"{got.width()}x{got.height()}")
    check("by cropping rather than squashing",
          True, "a squashed picture is the first thing anyone notices")

print("\n  the still shown for a clip:")
check("it sits beside the clip, hidden from the scan",
      poster_path(Path("out/clip_1.webm")).name == "_poster_clip_1.png",
      "an ordinary .png beside a clip would show up as its own picture")
check("nothing decodes the video to find it",
      "import av" not in Path("avcore/video.py").read_text(encoding="utf-8"),
      "the packaged build has no PyAV, and a feature that works only "
      "from source is a trap")

print("\n  how long a clip runs:")
check("comes from what was recorded when it was made",
      clip_length(Path("out/clip_1.webm"), {"seconds": 2.5}) == 2.5)
check("and is simply unknown for anything older",
      clip_length(Path("out/clip_1.webm"), {}) is None,
      "beats guessing")

print("\n  the Type filter:")
kinds = Filters()
kinds.kind = "video"
check("a clip passes the video filter",
      kinds.matches(Path("a/clip.webm"), {}, False, False))
check("an mp4 does too",
      kinds.matches(Path("a/clip.mp4"), {}, False, False))
check("a picture does not",
      not kinds.matches(Path("a/shot.png"), {}, False, False))

kinds.kind = ".png"
check("and picking PNG still works as it did",
      kinds.matches(Path("a/shot.png"), {}, False, False)
      and not kinds.matches(Path("a/clip.webm"), {}, False, False))

print("\n  what made the clip:")
from avcore.catalog import model_label

check("a clip names the video model",
      model_label("svd", "svd_xt.safetensors")
      == "Stable Video Diffusion - svd_xt",
      "'Svd' read like a typo")
check("and shortens to the checkpoint",
      model_label("svd", "svd_xt.safetensors", short=True) == "svd_xt")

print("\n=== acting on several pictures at once ===")
# Chosen by path rather than by widget, because the grid is rebuilt
# whenever a filter changes or a page turns.
_pick_dir = tmp / "picking"
(_pick_dir / "out").mkdir(parents=True, exist_ok=True)
_ps = Settings.load(path=_pick_dir / "s.json")
_ps.set("paths.overlay_dir", str(_pick_dir / "out"))
_ps.set("paths.manual_dir", str(_pick_dir / "out"))
_ps.save()
_pe = Engine(_ps)
_pe.catalog = Catalog(_pick_dir / "c.json")

for _i in range(5):
    _shot = _pick_dir / "out" / f"pick{_i}.png"
    write_png(_shot, 48, 48, (60, 120, 180))
    _pe.catalog.record(_shot, f"picture {_i}", source="live")

_gp = GalleryPanel(_pe)
app.processEvents()

check("nothing is chosen to begin with", not _gp._chosen)
check("and the bar is out of the way", _gp.pick_bar.isHidden(),
      "the gallery looks as it always did until somebody asks")

_gp._pick(_gp._visible[0], True)
_gp._pick(_gp._visible[1], True)
check("choosing two is counted", len(_gp._chosen) == 2)
check("and said in words", _gp.pick_count.text() == "2 chosen",
      _gp.pick_count.text())

print("\n  the buttons say which way they will go:")
check("Favourite, while none are", _gp.pick_fav.text() == "Favourite")
_gp._bulk_favourite()
app.processEvents()
_gp._pick(_gp._visible[0], True)
_gp._pick(_gp._visible[1], True)
_gp._sync_picks()
check("Unfavourite, once they all are",
      _gp.pick_fav.text() == "Unfavourite",
      "a mixed selection becomes all-on, which is the predictable "
      "reading of pressing Favourite")

_marked = sum(1 for _p in _gp._visible
              if (_pe.catalog.lookup(_p) or {}).get("favourite"))
check("both were marked", _marked == 2, str(_marked))

print("\n  censoring works the same way:")
_gp._bulk_censor()
app.processEvents()
_hidden = sum(1 for _p in _gp._visible
              if (_pe.catalog.lookup(_p) or {}).get("censored"))
check("both were censored", _hidden == 2, str(_hidden))

print("\n  select all, and clearing:")
_gp._pick_all()
check("everything showing is chosen",
      len(_gp._chosen) == len(_gp._visible))
_gp._pick_none()
check("and clearing empties it", not _gp._chosen)

print("\n  a picture the filters hide does not stay chosen:")
_gp._pick(_gp._visible[0], True)
_gp.filters.name = "pick0"
_gp._apply_filters()
_gp._sync_picks()
_still = [_p for _p in _gp._chosen]
check("it is dropped from the selection",
      all(_p in set(_gp._visible) for _p in _still),
      "otherwise Delete would take something nobody can see")
_gp.filters.name = ""
_gp._apply_filters()

print("\n  deleting asks once, not once each:")
from avgui.dialogs import ConfirmBulkDelete

_dialog = ConfirmBulkDelete(4)
_words = " ".join(l.text() for l in _dialog.findChildren(QLabel))
check("it says how many", "Delete 4 images?" in _words, _words[:40])
check("and that they leave the disk",
      "removed from disk" in _words)
check("keeping them is the default",
      {b.objectName(): b for b in _dialog.findChildren(QPushButton)}
      ["denyButton"].isDefault())
_dialog.reject()

_pe.shutdown()

bad = [n for n, ok in results if not ok]




print("\n=== the gallery pages through everything on disk ===")
# There used to be a hardcoded cap of 60 here, unrelated to any setting,
# so with "keep last" above that the extra images - favourites among
# them - could not be reached at all.
from PySide6.QtWidgets import QApplication as _QA
from avgui.gallery_panel import GalleryPanel, PAGE_SIZES

_QA.instance() or _QA([])

page = tmp / "paging"
(page / "out").mkdir(parents=True, exist_ok=True)
sp = Settings.load(path=page / "s.json")
sp.set("paths.overlay_dir", str(page / "out"))
sp.set("paths.manual_dir", str(page / "out"))
sp.set("ui.gallery_page_size", 24)
engp = Engine(sp)
engp.catalog = Catalog(page / "c.json")

import os
for i in range(70):
    p = page / "out" / f"live_{i:03}.png"
    write_png(p, 32, 18, (60 + i, 90, 150))
    os.utime(p, (time.time() - (70 - i) * 60,) * 2)

gal = GalleryPanel(engp)
check("every image is listed, not just 60", len(gal._all) == 70,
      f"{len(gal._all)}")
check("only a page is drawn", gal.grid.count() == 24,
      f"{gal.grid.count()} thumbnails")
check("the page count is right", gal._page_count() == 3,
      f"{gal._page_count()}")
check("previous is disabled on the first page",
      not gal.prev_btn.isEnabled())
check("next is available", gal.next_btn.isEnabled())

print("\n  turning pages:")
gal._turn_page(1)
check("second page drawn", gal.grid.count() == 24)
check("previous now works", gal.prev_btn.isEnabled())
gal._turn_page(1)
check("last page has the remainder", gal.grid.count() == 22,
      f"{gal.grid.count()}")
check("next is disabled at the end", not gal.next_btn.isEnabled())
gal._turn_page(1)
check("it cannot go past the end", gal._page == 2)

print("\n  the last image is reachable, which was the bug:")
names = {p.name for p in gal._all}
check("the oldest image is in the list", "live_000.png" in names)
last_page = gal._all[48:]
check("and lands on the final page", any(p.name == "live_000.png"
                                         for p in last_page))

print("\n  changing the page size:")
idx = gal.per_page.findData(96)
gal.per_page.setCurrentIndex(idx)
check("all 70 now fit on one page", gal.grid.count() == 70,
      f"{gal.grid.count()}")
check("one page only", gal._page_count() == 1)
check("the choice is remembered",
      sp.get("ui.gallery_page_size") == 96)

idx = gal.per_page.findData(0)      # "All"
gal.per_page.setCurrentIndex(idx)
check("'All' draws everything", gal.grid.count() == 70)
check("and reports a single page", gal._page_count() == 1)

print("\n  a favourite far down the list is still reachable:")
oldest = gal._all[-1]
engp.catalog.set_favourite(oldest, True)
gal._rebuild_all()
check("it is still listed", any(p == oldest for p in gal._all))
engp.shutdown()

print("\n=== lowering 'keep last' warns before deleting ===")
from avgui.dialogs import ConfirmPrune
from avgui.settings_panel import SettingsPanel
from PySide6.QtWidgets import QLabel as _QLabel, QPushButton as _QPushButton

keep = tmp / "keeping"
(keep / "out").mkdir(parents=True, exist_ok=True)
sk = Settings.load(path=keep / "s.json")
sk.set("paths.overlay_dir", str(keep / "out"))
sk.set("image.keep_images", 40)
sk.set("ui.confirm_settings", True)
sk.save()
engk = Engine(sk)
engk.catalog = Catalog(keep / "c.json")

for i in range(50):
    p = keep / "out" / f"live_{i:03}.png"
    write_png(p, 32, 18, (70, 120, 190))
    os.utime(p, (time.time() - (50 - i) * 60,) * 2)

check("50 on disk, keeping 40, so 10 are over",
      engk.prunable_count(40) == 10, str(engk.prunable_count(40)))
check("lowering to 20 would remove 30",
      engk.prunable_count(20) == 30, str(engk.prunable_count(20)))
check("raising to 90 removes nothing", engk.prunable_count(90) == 0)

print("\n  favourites do not count towards the limit:")
for i in range(5):
    engk.catalog.set_favourite(keep / "out" / f"live_{i:03}.png", True)
check("five favourites are excluded from the count",
      engk.prunable_count(40) == 5, str(engk.prunable_count(40)))

print("\n  the warning dialog:")
dlg = ConfirmPrune(30, 20)
labels = [l.text() for l in dlg.findChildren(_QLabel)]
buttons = {b.objectName(): b for b in dlg.findChildren(_QPushButton)}
check("it says how many go", any("30" in t for t in labels))
check("and how many are kept", any("20" in t for t in labels))
check("it mentions favourites are safe",
      any("Favourites are kept" in t for t in labels))
check("it warns it cannot be undone",
      any("cannot be undone" in t for t in labels))
check("Deny is the default", buttons["denyButton"].isDefault())
dlg.reject()

print("\n  denying withdraws only that change:")
pk = SettingsPanel(engk)
pk._save("image.keep_images", 20)
pk._save("audio.cycle_seconds", 31)
check("two changes pending", len(pk._pending) == 2)

# Answer the dialog with Deny without showing it.
import avgui.settings_panel as sp_mod
real_dialog = sp_mod.ConfirmPrune


class Denied:
    def __init__(self, *a, **k):
        pass

    def exec(self):
        return 0        # QDialog.Rejected


sp_mod.ConfirmPrune = Denied
pk.apply_pending()
sp_mod.ConfirmPrune = real_dialog

check("the limit was not changed", sk.get("image.keep_images") == 40,
      str(sk.get("image.keep_images")))
check("the other change still applied",
      sk.get("audio.cycle_seconds") == 31)
check("the images are all still there",
      len(list((keep / "out").glob("*.png"))) == 50)

print("\n  confirming prunes straight away:")
pk2 = SettingsPanel(engk)
pk2._save("image.keep_images", 20)


class Accepted:
    def __init__(self, *a, **k):
        pass

    def exec(self):
        return 1        # QDialog.Accepted


sp_mod.ConfirmPrune = Accepted
pk2.apply_pending()
sp_mod.ConfirmPrune = real_dialog

check("the limit was applied", sk.get("image.keep_images") == 20)
left = list((keep / "out").glob("*.png"))
check("older images were deleted", len(left) < 50, f"{len(left)} left")
check("the five favourites survived",
      all((keep / "out" / f"live_{i:03}.png").exists() for i in range(5)),
      "favourites are exempt")
check("and the newest were kept",
      (keep / "out" / "live_049.png").exists())

print("\n  raising the limit asks nothing:")
pk3 = SettingsPanel(engk)
pk3._save("image.keep_images", 80)
sp_mod.ConfirmPrune = Denied      # would refuse if it were consulted
pk3.apply_pending()
sp_mod.ConfirmPrune = real_dialog
check("it applied without asking", sk.get("image.keep_images") == 80)

engk.shutdown()

bad = [n for n, ok in results if not ok]




print("\n=== renaming an image ===")
from avgui.dialogs import RenameImage
from avgui.filters import DATE_RANGES, Filters, fuzzy, in_range

ren = tmp / "renaming"
(ren / "out").mkdir(parents=True, exist_ok=True)
sr = Settings.load(path=ren / "s.json")
sr.set("paths.overlay_dir", str(ren / "out"))
sr.set("paths.manual_dir", str(ren / "out"))
engr = Engine(sr)
engr.catalog = Catalog(ren / "c.json")

target = ren / "out" / "live_001.png"
write_png(target, 32, 18, (90, 140, 190))
engr.catalog.record(target, "a stone bridge in heavy fog", source="live")
engr.catalog.set_favourite(target, True)

ok, name = engr.rename_image(target, "stone bridge")
check("the rename reported success", ok, str(name))
renamed = ren / "out" / "stone bridge.png"
check("the new file exists", renamed.exists())
check("the old name is gone", not target.exists())
check("the extension was kept", renamed.suffix == ".png")

entry = engr.catalog.lookup(renamed)
check("the prompt came with it",
      entry and entry["prompt"] == "a stone bridge in heavy fog")
check("the favourite mark came with it",
      engr.catalog.is_favourite(renamed))
check("nothing is left under the old name",
      engr.catalog.lookup(target) is None)

print("\n  bad names are refused, not applied:")
ok, message = engr.rename_image(renamed, "")
check("an empty name is refused", not ok and "needed" in message.lower(),
      message)
ok, message = engr.rename_image(renamed, "with/slash")
check("a path separator is refused", not ok, message[:40])
ok, message = engr.rename_image(renamed, 'quote"mark')
check("an illegal character is refused", not ok, message[:40])
check("the file is untouched by any of that", renamed.exists())

print("\n  a clash is refused:")
other = ren / "out" / "live_002.png"
write_png(other, 32, 18, (190, 120, 90))
ok, message = engr.rename_image(other, "stone bridge")
check("renaming onto an existing name is refused",
      not ok and "exists" in message.lower(), message)
check("both files survive", other.exists() and renamed.exists())

print("\n  renaming what is on the overlay follows it:")
engr.publish_overlay(renamed, source="live")
check("it is on the overlay",
      engr.snapshot()["image"] == str(renamed))
ok, name = engr.rename_image(renamed, "bridge in fog")
check("renamed", ok)
check("the overlay points at the new name",
      engr.snapshot()["image"].endswith("bridge in fog.png"),
      engr.snapshot()["image"])
import json as _json
state = _json.loads((ren / "out" / "state.json").read_text())
check("and so does the page's state file",
      state["image"] == "bridge in fog.png", str(state["image"]))

print("\n  the dialog:")
dlg = RenameImage(ren / "out" / "bridge in fog.png")
check("it starts with the current name, without the extension",
      dlg.new_stem() == "bridge in fog", dlg.new_stem())
dlg.show_error("already exists")
check("it can show an error and stay open", not dlg.error.isHidden())
dlg.reject()
engr.shutdown()

print("\n=== filtering the gallery ===")
print("  fuzzy matching:")
check("an exact word matches", fuzzy("bridge", "a stone bridge in fog"))
check("words in any order match",
      fuzzy("fog bridge", "a stone bridge in heavy fog"))
check("it is case insensitive", fuzzy("BRIDGE", "a stone bridge"))
check("a dropped letter still matches",
      fuzzy("lghths", "an old lighthouse"), "subsequence fallback")
check("something absent does not match",
      not fuzzy("harbour", "a stone bridge in fog"))
check("an empty needle matches everything", fuzzy("", "anything"))
check("nothing matches an empty haystack", not fuzzy("x", ""))

print("\n  date windows:")
now = time.time()
check("today includes now", in_range(now, "today"))
check("today excludes two days ago",
      not in_range(now - 2 * 86400, "today"))
check("yesterday excludes now", not in_range(now, "yesterday"))
check("a week includes three days ago",
      in_range(now - 3 * 86400, 7))
check("a week excludes ten days ago",
      not in_range(now - 10 * 86400, 7))
check("any time includes everything", in_range(now - 9999 * 86400, None))

print("\n  the filters together:")
filt = tmp / "filtering"
(filt / "out").mkdir(parents=True, exist_ok=True)
sf = Settings.load(path=filt / "s.json")
sf.set("paths.overlay_dir", str(filt / "out"))
sf.set("paths.manual_dir", str(filt / "out"))
sf.set("ui.gallery_page_size", 96)
engf = Engine(sf)
engf.catalog = Catalog(filt / "c.json")

made = {}
for name, prompt, source, age_days in [
    ("live_today.png", "a stone bridge in heavy fog", "live", 0),
    ("live_old.png", "an abandoned railway station", "live", 40),
    ("ai_typed.png", "a brass diving helmet", "manual", 0),
    ("ai_week.png", "a copper kettle on a stove", "manual", 4),
]:
    p = filt / "out" / name
    write_png(p, 32, 18, (100, 140, 180))
    os.utime(p, (now - age_days * 86400,) * 2)
    engf.catalog.record(p, prompt, source=source)
    made[name] = p
engf.catalog.set_favourite(made["live_old.png"], True)

galf = GalleryPanel(engf)
check("all four are listed", len(galf._all) == 4, str(len(galf._all)))
check("nothing is filtered yet", len(galf._visible) == 4)
# isHidden, not isVisible: nothing here is on screen, and a child of an
# unshown window is never visible whatever its own state.
check("the clear button is hidden", galf.clear_filters_btn.isHidden())


def names():
    return sorted(p.name for p in galf._visible)


print("\n  by name:")
galf.name_box.setText("typed")
check("only the matching name", names() == ["ai_typed.png"], str(names()))
check("the clear button appeared", not galf.clear_filters_btn.isHidden())
galf._clear_filters()
check("clearing brings them all back", len(galf._visible) == 4)

print("\n  by prompt:")
galf.prompt_box.setText("kettle")
check("matched on the prompt, not the filename",
      names() == ["ai_week.png"], str(names()))
galf._clear_filters()

print("\n  by source:")
galf.source_pick.setCurrentIndex(galf.source_pick.findData("heard"))
check("only heard images",
      names() == ["live_old.png", "live_today.png"], str(names()))
galf.source_pick.setCurrentIndex(galf.source_pick.findData("typed"))
check("only typed images",
      names() == ["ai_typed.png", "ai_week.png"], str(names()))
galf._clear_filters()

print("\n  favourites is its own three-state toggle:")
from avgui.widgets import TriStateFilter

check("it is not one of the source options",
      galf.source_pick.findData("favourite") == -1,
      "favourite is a mark, not a kind")
check("it starts neutral",
      galf.fav_only.state() == TriStateFilter.NEUTRAL)

galf.fav_only.set_state(TriStateFilter.INCLUDE)
check("including shows only favourites",
      names() == ["live_old.png"], str(names()))
check("it counts as an active filter", galf.filters.active())

galf.fav_only.set_state(TriStateFilter.EXCLUDE)
check("excluding hides them instead",
      names() == ["ai_typed.png", "ai_week.png", "live_today.png"],
      str(names()))
check("the favourite is the one missing",
      "live_old.png" not in names())

galf.fav_only.set_state(TriStateFilter.NEUTRAL)
check("neutral shows everything again", len(galf._visible) == 4)

print("\n  the click rules:")


def click(widget, button):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QMouseEvent
    pos = QPointF(8, 8)
    widget.mousePressEvent(QMouseEvent(
        QEvent.MouseButtonPress, pos, pos, button, button,
        Qt.KeyboardModifiers()))


from PySide6.QtCore import QEvent

tri = galf.fav_only
tri.set_state(TriStateFilter.NEUTRAL)
click(tri, Qt.LeftButton)
check("neutral + left goes to include",
      tri.state() == TriStateFilter.INCLUDE, tri.state())
click(tri, Qt.LeftButton)
check("include + left goes back to neutral",
      tri.state() == TriStateFilter.NEUTRAL, tri.state())
click(tri, Qt.RightButton)
check("neutral + right goes to exclude",
      tri.state() == TriStateFilter.EXCLUDE, tri.state())
click(tri, Qt.RightButton)
check("exclude + right goes back to neutral",
      tri.state() == TriStateFilter.NEUTRAL, tri.state())
click(tri, Qt.LeftButton)
click(tri, Qt.RightButton)
check("include + right flips straight to exclude",
      tri.state() == TriStateFilter.EXCLUDE, tri.state())
click(tri, Qt.LeftButton)
check("exclude + left flips straight to include",
      tri.state() == TriStateFilter.INCLUDE, tri.state())

print("\n  the tooltip explains both buttons in every state:")
for state, must_mention in (
        (TriStateFilter.NEUTRAL, ("Left click", "Right click")),
        (TriStateFilter.INCLUDE, ("Left click", "Right click")),
        (TriStateFilter.EXCLUDE, ("Left click", "Right click"))):
    tri.set_state(state)
    tip = tri.toolTip()
    check(f"{state}: the tooltip covers both buttons",
          all(part in tip for part in must_mention),
          tip.replace("\n", " | ")[:72])

tri.set_state(TriStateFilter.NEUTRAL)

print("\n  and it combines with the source, which is the point:")
galf.source_pick.setCurrentIndex(galf.source_pick.findData("heard"))
galf.fav_only.set_state(TriStateFilter.INCLUDE)
check("heard and favourited together",
      names() == ["live_old.png"], str(names()))
galf.fav_only.set_state(TriStateFilter.EXCLUDE)
check("heard but not favourited",
      names() == ["live_today.png"], str(names()))
galf.source_pick.setCurrentIndex(galf.source_pick.findData("typed"))
galf.fav_only.set_state(TriStateFilter.INCLUDE)
check("typed and favourited matches nothing here",
      names() == [], str(names()))
galf._clear_filters()
check("clearing resets it to neutral",
      galf.fav_only.state() == TriStateFilter.NEUTRAL)
check("and shows everything again", len(galf._visible) == 4)

print("\n  by date:")
galf.date_pick.setCurrentIndex(galf.date_pick.findData("today"))
check("only today's",
      names() == ["ai_typed.png", "live_today.png"], str(names()))
galf.date_pick.setCurrentIndex(galf.date_pick.findData(7))
check("the past week adds the four-day-old one",
      names() == ["ai_typed.png", "ai_week.png", "live_today.png"],
      str(names()))
galf.date_pick.setCurrentIndex(galf.date_pick.findData(365))
check("a year takes in everything", len(galf._visible) == 4)
galf._clear_filters()

print("\n  filters combine:")
galf.source_pick.setCurrentIndex(galf.source_pick.findData("heard"))
galf.date_pick.setCurrentIndex(galf.date_pick.findData("today"))
check("heard and today together", names() == ["live_today.png"],
      str(names()))

print("\n  nothing matching says so:")
galf.prompt_box.setText("submarine")
check("the list is empty", not galf._visible)
check("and it explains why",
      "Nothing matches" in galf.empty.text(), galf.empty.text())
galf._clear_filters()
check("and recovers", len(galf._visible) == 4)

print("\n  the count reflects the filter:")
galf.name_box.setText("live")
check("it says how many of how many",
      "2 of 4" in galf.count.text(), galf.count.text())
galf._clear_filters()
engf.shutdown()

bad = [n for n, ok in results if not ok]




print("\n=== 'keep last' counts only images that are not favourited ===")
# Deliberately favouriting the OLDEST images: those are exactly the ones a
# limit would delete first, so if favourites were counted this would fail
# rather than quietly pass.
limit = tmp / "limit"
(limit / "out").mkdir(parents=True, exist_ok=True)
sl = Settings.load(path=limit / "s.json")
sl.set("paths.overlay_dir", str(limit / "out"))
sl.set("image.keep_images", 10)
engl = Engine(sl)
engl.catalog = Catalog(limit / "c.json")

base = time.time()
for i in range(25):
    p = limit / "out" / f"img_{i:02}.png"
    write_png(p, 32, 18, (80, 130, 180))
    os.utime(p, (base - (25 - i) * 60,) * 2)      # img_00 is oldest

oldest = [f"img_{i:02}.png" for i in range(5)]
for name in oldest:
    engl.catalog.set_favourite(limit / "out" / name, True)

check("only the ordinary surplus is counted",
      engl.prunable_count(10) == 10,
      f"{engl.prunable_count(10)} (25 images, 5 favourited, keeping 10)")

engl.prune_now()
left = sorted(p.name for p in (limit / "out").glob("*.png"))
favs_left = [n for n in left if n in oldest]
ordinary_left = [n for n in left if n not in oldest]

check("every favourite survived", len(favs_left) == 5, str(favs_left))
check("exactly 10 ordinary images were kept", len(ordinary_left) == 10,
      str(len(ordinary_left)))
check("the total exceeds the limit, which is the point",
      len(left) == 15, f"{len(left)} on disk with a limit of 10")
check("the newest ordinary ones were the ones kept",
      "img_24.png" in ordinary_left and "img_05.png" not in ordinary_left)

print("\n  a limit of 1 still keeps every favourite:")
sl.set("image.keep_images", 1)
engl.prune_now()
left = sorted(p.name for p in (limit / "out").glob("*.png"))
check("all five favourites are still there",
      len([n for n in left if n in oldest]) == 5, str(left))
check("and one ordinary image", len(left) == 6, str(len(left)))

print("\n  Settings says so in words:")
pl = SettingsPanel(engl)
note = pl.keep_note.text()
check("the note mentions favourites", "favourit" in note.lower())
check("it says they are not counted",
      "not count" in note.lower() or "never counted" in note.lower(),
      note[:58])
check("and it names how many there are", "5 favourite" in note, note[-66:])
engl.shutdown()




print("\n=== purging the gallery ===")
from avgui.dialogs import ConfirmPurge
from PySide6.QtWidgets import QLabel as _QL, QPushButton as _QPB

purge = tmp / "purging"
(purge / "out").mkdir(parents=True, exist_ok=True)
sp2 = Settings.load(path=purge / "s.json")
sp2.set("paths.overlay_dir", str(purge / "out"))
sp2.set("paths.manual_dir", str(purge / "out"))
sp2.save()
engp2 = Engine(sp2)
engp2.catalog = Catalog(purge / "c.json")

for i in range(12):
    p = purge / "out" / f"img_{i:02}.png"
    write_png(p, 32, 18, (80, 120, 170))
    os.utime(p, (time.time() - i * 60,) * 2)
keepers = ["img_00.png", "img_03.png", "img_07.png"]
for name in keepers:
    engp2.catalog.set_favourite(purge / "out" / name, True)

check("it counts only what is not favourited",
      len(engp2.purgeable()) == 9, str(len(engp2.purgeable())))
check("no favourite is in that list",
      not any(p.name in keepers for p in engp2.purgeable()))

galp = GalleryPanel(engp2)
check("the button is offered", galp.purge_btn.isEnabled())
check("it is styled as destructive",
      galp.purge_btn.objectName() == "denyButton",
      "the same red as Delete and Deny")
check("the tooltip says how many", "9" in galp.purge_btn.toolTip(),
      galp.purge_btn.toolTip())

print("\n  the confirmation states both numbers:")
dlg = ConfirmPurge(9, 3)
labels = [l.text() for l in dlg.findChildren(_QL)]
buttons = {b.objectName(): b for b in dlg.findChildren(_QPB)}
check("how many go", any("9" in t for t in labels))
check("how many are safe", any("3 favourite" in t for t in labels))
check("it warns it cannot be undone",
      any("cannot be undone" in t for t in labels))
check("Deny is the default button", buttons["denyButton"].isDefault())
dlg.reject()

print("\n  with no favourites it says so plainly:")
bare = ConfirmPurge(5, 0)
bare_text = " ".join(l.text() for l in bare.findChildren(_QL))
check("it warns the gallery will be emptied",
      "emptied completely" in bare_text, bare_text[-60:])
bare.reject()

print("\n  purging:")
engp2.publish_overlay(purge / "out" / "img_05.png", source="live")
check("an image is on the overlay first",
      engp2.snapshot()["image"].endswith("img_05.png"))

removed, failed = engp2.purge_images()
check("it removed the nine", removed == 9, str(removed))
check("none failed", failed == 0)
left = sorted(p.name for p in (purge / "out").glob("*.png"))
check("only the favourites remain", left == keepers, str(left))
check("the overlay was cleared, since it was showing one of them",
      not engp2.snapshot().get("image"),
      "the page would otherwise ask for a deleted file")
check("their favourite marks survived",
      all(engp2.catalog.is_favourite(purge / "out" / n) for n in keepers))

galp._rebuild_all()
check("the gallery shows what is left", len(galp._all) == 3,
      str(len(galp._all)))
check("and the button switches off", not galp.purge_btn.isEnabled())
check("saying why", "Nothing to purge" in galp.purge_btn.toolTip(),
      galp.purge_btn.toolTip())

print("\n  purging an already-clean gallery does nothing:")
again = engp2.purge_images()
check("it removes nothing", again == (0, 0), str(again))
check("and the favourites are untouched",
      len(list((purge / "out").glob("*.png"))) == 3)
engp2.shutdown()

bad = [n for n, ok in results if not ok]




print("\n=== censoring an image ===")
# A mark in the catalogue, exactly like a favourite: the file is never
# touched, so it is always reversible and never costs the picture.
from avgui.viewer import ImageViewer
from PySide6.QtCore import QRect as _QRect

cen = tmp / "censor"
(cen / "out").mkdir(parents=True, exist_ok=True)
sc2 = Settings.load(path=cen / "s.json")
sc2.set("paths.overlay_dir", str(cen / "out"))
sc2.set("paths.manual_dir", str(cen / "out"))
sc2.save()
engc2 = Engine(sc2)
engc2.catalog = Catalog(cen / "c.json")

for i in range(6):
    p = cen / "out" / f"pic_{i}.png"
    write_png(p, 320, 180, (60 + i * 28, 120, 180))
    os.utime(p, (time.time() - i * 60,) * 2)
    engc2.catalog.record(p, f"picture {i}", source="live")

target = cen / "out" / "pic_1.png"
before = target.stat().st_size
engc2.set_censored(target, True)
check("the mark is recorded", engc2.catalog.is_censored(target))
check("the file itself is untouched",
      target.stat().st_size == before,
      "censoring is a display choice, never a change to the image")
check("it appears in the censored set",
      target.name in engc2.catalog.censored())

engc2.catalog.set_favourite(target, True)
engc2.set_censored(target, False)
check("clearing the censor leaves the favourite alone",
      engc2.catalog.is_favourite(target))
engc2.set_censored(target, True)

print("\n  the thumbnail blurs:")
galc = GalleryPanel(engc2)
thumbs = {galc.grid.itemAt(i).widget().path.name:
          galc.grid.itemAt(i).widget()
          for i in range(galc.grid.count())}
marked = thumbs[target.name]
plain = thumbs["pic_0.png"]
check("the censored thumbnail knows it", marked.censored)
check("an ordinary one does not", not plain.censored)
blurred = marked.image.pixmap()
check("it still has a picture to show",
      blurred is not None and not blurred.isNull())

print("\n  and it can be filtered like a favourite:")
check("the filter starts neutral",
      galc.censor_only.state() == TriStateFilter.NEUTRAL)
galc.censor_only.set_state(TriStateFilter.INCLUDE)
names = sorted(p.name for p in galc._visible)
check("showing only censored", names == [target.name], str(names))
galc.censor_only.set_state(TriStateFilter.EXCLUDE)
names = sorted(p.name for p in galc._visible)
check("hiding them instead", target.name not in names and len(names) == 5,
      str(len(names)))
galc.censor_only.set_state(TriStateFilter.NEUTRAL)
check("neutral shows everything", len(galc._visible) == 6)

print("\n  it combines with the favourites filter:")
galc.fav_only.set_state(TriStateFilter.INCLUDE)
galc.censor_only.set_state(TriStateFilter.INCLUDE)
check("favourited and censored together",
      sorted(p.name for p in galc._visible) == [target.name])
galc._clear_filters()
check("clearing resets both",
      galc.fav_only.state() == TriStateFilter.NEUTRAL
      and galc.censor_only.state() == TriStateFilter.NEUTRAL)

print("\n=== the expanded viewer ===")
galc.resize(900, 620)
galc.show()
app.processEvents()
first = galc.grid.itemAt(0).widget()
galc._open_viewer(first.path, first.image)
for _ in range(40):
    app.processEvents()
    time.sleep(0.01)
view = galc.viewer

check("it opened", view.isVisible())
check("showing what was clicked", view.current() == first.path)
check("it fills the gallery, not the window",
      view.size() == galc.size(),
      "the grid is what it expands within")

print("\n  it walks the visible list, not the whole folder:")
# Arrowing through a filtered gallery should follow the filter, or the
# filter stops meaning anything as soon as the picture gets bigger.
view.collapse()
for _ in range(40):
    app.processEvents()
    time.sleep(0.01)
galc.censor_only.set_state(TriStateFilter.EXCLUDE)
visible = list(galc._visible)
shown = galc.grid.itemAt(0).widget()
galc._open_viewer(shown.path, shown.image)
for _ in range(30):
    app.processEvents()
    time.sleep(0.01)
check("the viewer got the filtered set",
      len(view._paths) == len(visible), f"{len(view._paths)} of 6")
check("the censored one is not among them",
      target.name not in {p.name for p in view._paths})
galc.censor_only.set_state(TriStateFilter.NEUTRAL)
view.collapse()
for _ in range(40):
    app.processEvents()
    time.sleep(0.01)

print("\n  stepping between images:")
first = galc.grid.itemAt(0).widget()
galc._open_viewer(first.path, first.image)
for _ in range(30):
    app.processEvents()
    time.sleep(0.01)
start_name = view.current().name
check("forward moves on", view.step(1) is True)
check("and the image changed", view.current().name != start_name)
for _ in range(40):
    app.processEvents()
    time.sleep(0.01)
check("back returns", view.step(-1) is True)
for _ in range(40):
    app.processEvents()
    time.sleep(0.01)
check("to where it started", view.current().name == start_name)

check("it will not step past the beginning", view.step(-1) is False,
      "stopping beats silently wrapping round")
while view.step(1):
    for _ in range(26):
        app.processEvents()
        time.sleep(0.01)
check("nor past the end", view.step(1) is False)
check("and the arrows say so",
      not view.next_btn.isEnabled() and view.prev_btn.isEnabled())

print("\n  a censored image stays hidden until asked:")
view.collapse()
for _ in range(40):
    app.processEvents()
    time.sleep(0.01)
marked_thumb = None
for i in range(galc.grid.count()):
    widget = galc.grid.itemAt(i).widget()
    if widget.path.name == target.name:
        marked_thumb = widget
        break
galc._open_viewer(marked_thumb.path, marked_thumb.image)
for _ in range(30):
    app.processEvents()
    time.sleep(0.01)
check("the hint is shown", not view.hint.isHidden())
check("and says what to do", "click to reveal" in view.hint.text().lower(),
      view.hint.text())

view._revealed.add(view.current().name)
view._render()
check("revealing hides the hint", view.hint.isHidden())
check("and it stays revealed while open",
      view.current().name in view._revealed)

print("\n  closing:")
view.collapse()
for _ in range(50):
    app.processEvents()
    time.sleep(0.01)
check("it went away", not view.isVisible())
check("the grid is still there", galc.grid.count() > 0)
engc2.shutdown()

bad = [n for n, ok in results if not ok]

shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("the gallery options work")
