"""
Gallery panel: recent images, with a way to put any of them back on the
overlay. Thumbnails are cached by path so scrolling doesn't re-decode.
"""

import os
import subprocess
from datetime import datetime
import time
from pathlib import Path

from PySide6.QtCore import (
    QPoint, QRect, QSize, Qt, QThread, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QProgressBar,
    QApplication, QComboBox, QDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import theme
from .dialogs import ConfirmDelete, ConfirmPurge, RenameImage
from avcore.catalog import model_label

from .filters import DATE_RANGES, SOURCES, Filters
from .viewer import ImageViewer
from .widgets import over_checkerboard, HoverCaption, TriStateFilter

# Clips sit in the same folder as the pictures they were made from, so
# the gallery is one place rather than two.
VIDEO_EXTS = (".webm", ".mp4")
EXTS = (".png", ".jpg", ".jpeg", ".webp") + VIDEO_EXTS
COLS = 3
THUMB = QSize(210, 118)

# How many thumbnails to draw at once. Building one is not free, so a few
# hundred at a time would make the panel crawl - but every image on disk
# is still listed and reachable by paging.
PAGE_SIZES = [("12", 12), ("24", 24), ("48", 48), ("96", 96), ("All", 0)]


class AnimateWorker(QThread):
    """
    Makes a clip, off the interface thread.

    Two minutes of work. Anything blocking here would freeze the window
    for that whole time, which is the fault this project has tripped
    over more than once.
    """

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, engine, source, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.source = source

    def run(self):
        try:
            self.done.emit(self.engine.animate_image(self.source))
        except Exception as exc:
            self.failed.emit(str(exc))


class Thumb(QWidget):
    def __init__(self, path, on_push, entry=None, on_changed=None,
                 engine=None, on_expand=None, on_animate=None,
                 on_pick=None, chosen=(), parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.engine = engine
        self.on_changed = on_changed or (lambda: None)
        self.on_expand = on_expand
        self.on_animate = on_animate
        self.on_pick = on_pick or (lambda _path, _on: None)
        self.entry = entry or {}
        self.censored = bool(self.entry.get("censored"))
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        self.image = HoverCaption(THUMB)
        self.is_video = self.path.suffix.lower() in VIDEO_EXTS
        pm = self._thumbnail()
        self.full = pm
        if not pm.isNull():
            shown = pm.scaled(THUMB, Qt.KeepAspectRatio,
                              Qt.SmoothTransformation)
            if self.censored:
                shown = self._blurred(shown)
            # Same pattern as the preview: a cut-out on a dark tile is
            # indistinguishable from a picture with a black background.
            shown = over_checkerboard(shown, square=8)
            self.image.setPixmap(shown)
        self.image.set_favourite(bool(self.entry.get("favourite")))
        self.image.cog_clicked.connect(self._open_menu)
        self.image.expand_clicked.connect(self._expand)

        if entry and entry.get("prompt"):
            when = entry.get("at")
            stamp = (datetime.fromtimestamp(when).strftime("%d %b, %H:%M")
                     if when else "")
            source = entry.get("source") or ""
            made_by = model_label(entry.get("backend"), entry.get("model"),
                                  short=True)
            detail = "   ".join(x for x in (
                "typed" if source == "manual" else "heard", stamp,
                made_by) if x)
            if self.is_video:
                from avcore.video import clip_length

                seconds = clip_length(self.path, self.entry)
                detail = "   ".join(
                    x for x in ("video",
                                f"{seconds:.1f}s" if seconds else "",
                                stamp, made_by) if x)
            self.image.set_caption(entry["prompt"], detail)
            # The full name in the tooltip: a checkpoint name is often
            # too long for the caption but is exactly what someone wants
            # when asking "how did I get this one?"
            full = model_label(entry.get("backend"), entry.get("model"))
            self.image.setToolTip(
                f"{entry['prompt']}\n\n{full}" if full else entry["prompt"])
        else:
            # Images made before the catalogue existed, or ones dropped
            # into the folder by hand. Saying so is better than an empty
            # hover that looks broken.
            self.image.set_caption("No prompt was recorded for this image.")

        col.addWidget(self.image)

        row = QHBoxLayout()
        row.setSpacing(4)

        # Always visible rather than on hover: a tick that only appears
        # when the mouse is over the picture is one nobody finds, and
        # the row has space for it.
        self.pick = QCheckBox()
        self.pick.setToolTip("Choose this one, to act on several at once")
        self.pick.setChecked(self.path in chosen)
        self.pick.toggled.connect(
            lambda on: self.on_pick(self.path, bool(on)))
        row.addWidget(self.pick)

        name = QLabel()
        name.setObjectName("fieldLabel")
        name.setToolTip(str(self.path))
        # Elided in the middle: the start says what made it and the end
        # is the timestamp that tells one from another, so the middle is
        # the part worth losing.
        metrics = QFontMetrics(name.font())
        name.setText(metrics.elidedText(self.path.name, Qt.ElideMiddle,
                                        THUMB.width() - 78))
        row.addWidget(name, 1)
        push = QPushButton("Show")
        push.setToolTip("Put this image on the overlay")
        push.clicked.connect(lambda: on_push(self.path))
        row.addWidget(push)
        col.addLayout(row)
        # Without this the cell stretches to the grid column, which on a
        # wide window leaves each Show button stranded halfway to the next
        # thumbnail.
        self.setFixedWidth(THUMB.width())

    # ---- the cog menu --------------------------------------------------

    def _catalog(self):
        return getattr(self.engine, "catalog", None)

    def _open_menu(self):
        menu = QMenu(self)
        prompt = (self.entry or {}).get("prompt") or ""
        favourite = bool((self.entry or {}).get("favourite"))

        copy = menu.addAction("Copy prompt")
        copy.setEnabled(bool(prompt))
        if not prompt:
            copy.setToolTip("No prompt was recorded for this image")
        copy.triggered.connect(self._copy_prompt)

        fav = menu.addAction("Unfavourite photo" if favourite
                             else "Favourite photo")
        fav.triggered.connect(self._toggle_favourite)

        menu.addAction("Uncensor photo" if self.censored
                       else "Censor photo", self._toggle_censored)

        menu.addAction("Rename photo", self._rename)

        # Only when the model is actually there. Offering it otherwise
        # would mean a menu entry whose only outcome is an apology, and
        # the whole point of the opt-in is that people who do not want
        # video never see it.
        from avcore.setup import video_installed

        if not self.is_video and self.engine is not None \
                and video_installed(self.engine.s):
            menu.addSeparator()
            animate = menu.addAction("Animate this...")
            animate.setToolTip(
                "Make a short clip from this picture")
            animate.triggered.connect(self._animate)

        menu.addSeparator()
        delete = menu.addAction("Delete photo")
        # Styled directly rather than through the stylesheet: Qt does not
        # apply property selectors to menu items reliably across styles.
        delete.setIcon(self._dot(theme.DANGER))
        delete.triggered.connect(self._delete)

        # Hold the hover open, or the caption and cog vanish the moment
        # the pointer moves onto the menu.
        self.image.set_menu_open(True)
        menu.aboutToHide.connect(lambda: self.image.set_menu_open(False))
        menu.exec(self.image.mapToGlobal(
            self.image.cog.geometry().bottomLeft()))

    @staticmethod
    def _dot(colour):
        """A small colour swatch, used to mark the destructive action."""
        pm = QPixmap(10, 10)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(colour))
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 8, 8)
        p.end()
        return QIcon(pm)

    def _animate(self):
        """Ask the gallery to turn this picture into a clip."""
        asked = getattr(self, "on_animate", None)
        if asked is not None:
            asked(self.path)

    def _copy_prompt(self):
        prompt = (self.entry or {}).get("prompt") or ""
        if prompt:
            QApplication.clipboard().setText(prompt)

    def _expand(self):
        """Ask the gallery to open this image, starting from here."""
        if callable(self.on_expand):
            self.on_expand(self.path, self.image)

    def _thumbnail(self):
        """
        Something to show in the grid.

        A clip cannot be drawn by a label, so its first frame stands in.
        Decoded once and kept beside the clip, because decoding on every
        rebuild of the gallery would make scrolling crawl.
        """
        if not self.is_video:
            return QPixmap(str(self.path))

        from avcore.video import poster_path

        # Written beside the clip when it was made, with a leading
        # underscore so the gallery's own scan skips it. Nothing is
        # decoded here: the packaged build has no PyAV, and a thumbnail
        # that only appears when run from source would be a trap.
        still = poster_path(self.path)
        if still.exists():
            return QPixmap(str(still))
        # A clip from before posters were saved, or one whose still went
        # missing. A blank tile still carries the name and the menu,
        # which beats the item vanishing.
        return QPixmap()

    def _blurred(self, pixmap):
        """
        A heavily blurred copy of a thumbnail.

        Done by scaling right down and back up rather than with a blur
        filter: at thumbnail size the result is indistinguishable, it
        costs a fraction as much, and - the part that matters - shrinking
        to a dozen pixels genuinely discards the detail rather than
        hiding it behind something that could be undone.
        """
        if pixmap is None or pixmap.isNull():
            return pixmap
        small = pixmap.scaled(max(3, pixmap.width() // 26),
                             max(3, pixmap.height() // 26),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return small.scaled(pixmap.size(), Qt.IgnoreAspectRatio,
                            Qt.SmoothTransformation)

    def _toggle_censored(self):
        engine = self.engine
        if engine is None or not hasattr(engine, "set_censored"):
            return
        engine.set_censored(self.path, not self.censored)
        self.on_changed()

    def _toggle_favourite(self):
        catalog = self._catalog()
        if catalog is None:
            return
        now = not catalog.is_favourite(self.path)
        catalog.set_favourite(self.path, now)
        self.entry = catalog.lookup(self.path) or {}
        self.image.set_favourite(now)

    def _rename(self):
        """
        Ask for a new name, and keep asking if it is not usable.

        The dialog stays open on a clash or a bad character rather than
        closing and losing what was typed.
        """
        engine = self.engine
        if engine is None or not hasattr(engine, "rename_image"):
            return
        dialog = RenameImage(self.path, self.image.pixmap(), self)
        while dialog.exec() == QDialog.Accepted:
            ok, message = engine.rename_image(self.path, dialog.new_stem())
            if ok or not message:
                break
            dialog.show_error(message)
        else:
            return
        self.on_changed()

    def _delete(self):
        pixmap = self.image.pixmap()
        dialog = ConfirmDelete(self.path, pixmap, self)
        if dialog.exec() != QDialog.Accepted:
            return
        engine = self.engine
        if engine is not None and hasattr(engine, "delete_image"):
            engine.delete_image(self.path)
        else:
            try:
                self.path.unlink()
            except OSError:
                pass
        self.on_changed()


class GalleryPanel(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._all = []         # every image found, newest first
        self._visible = []     # what the filters allow
        # Chosen by path, not by widget: the grid is rebuilt whenever a
        # filter changes or a page turns, and a widget reference would
        # be forgotten the moment somebody narrowed the list to find the
        # next picture they wanted.
        self._chosen = set()
        self.filters = Filters()
        self.viewer = None
        self._cols = COLS      # recomputed from the panel width
        self._page = 0
        self._build()
        self.refresh()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("statusBar")
        bar.setFixedHeight(46)
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 12, 0)
        self.count = QLabel("No images yet")

        # Shown only while a clip is being made. At the top because it
        # is the answer to "what is it doing?", and that question is
        # asked of the whole window rather than of one thumbnail.
        self.clip_bar = QProgressBar()
        self.clip_bar.setTextVisible(True)
        self.clip_bar.hide()
        self.clip_stop = QPushButton("Stop")
        self.clip_stop.setObjectName("denyButton")
        self.clip_stop.clicked.connect(self._cancel_clip)
        self.clip_stop.hide()

        self._clip_timer = QTimer(self)
        self._clip_timer.setInterval(1000)
        self._clip_timer.timeout.connect(self._tick_clip)
        self._clip_started = 0.0
        self._clip_estimate = 1
        self._clip_name = ""
        row.addWidget(self.count)
        # Between the count and the buttons: it only appears while a
        # clip is being made, and takes the space nothing else wants.
        row.addWidget(self.clip_bar, 1)
        row.addWidget(self.clip_stop)
        row.addStretch(1)

        folder_btn = QPushButton("Open folder")
        folder_btn.clicked.connect(self._open_folder)
        row.addWidget(folder_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        row.addWidget(refresh_btn)

        # Destructive, so it wears the same red as Delete and Deny, and
        # sits apart from the two harmless buttons beside it.
        self.purge_btn = QPushButton("Purge")
        self.purge_btn.setObjectName("denyButton")
        self.purge_btn.clicked.connect(self._purge)
        row.addWidget(self.purge_btn)
        outer.addWidget(bar)
        outer.addWidget(self._filter_bar())
        # Under the filters, because what it acts on is whatever they
        # are showing.
        self.pick_bar = self._pick_bar()
        outer.addWidget(self.pick_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        holder = QWidget()
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(14, 14, 14, 14)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignTop)
        # Pack the columns to the left rather than spreading them across
        # a wide window, which would leave gaps between thumbnails.
        self.grid.setColumnStretch(40, 1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        self.empty = QLabel(
            "Images will appear here once something has been generated.")
        self.empty.setObjectName("previewEmpty")
        self.empty.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.empty)
        self.empty.hide()

        # --- paging -----------------------------------------------------
        # Everything on disk is listed; this decides how much of it is
        # drawn at once. Building a thumbnail is not free, so a few
        # hundred images at once would make the panel crawl.
        pager = QWidget()
        pager.setObjectName("statusBar")
        pager.setFixedHeight(44)
        prow = QHBoxLayout(pager)
        prow.setContentsMargins(14, 0, 12, 0)
        prow.setSpacing(8)

        prow.addWidget(QLabel("Show"))
        self.per_page = QComboBox()
        for label, value in PAGE_SIZES:
            self.per_page.addItem(label, value)
        saved = self.engine.s.get("ui.gallery_page_size", 24)
        idx = self.per_page.findData(saved)
        self.per_page.setCurrentIndex(idx if idx >= 0 else 1)
        self.per_page.currentIndexChanged.connect(self._pick_page_size)
        prow.addWidget(self.per_page)
        prow.addWidget(QLabel("per page"))

        prow.addStretch(1)

        self.prev_btn = QPushButton("Previous")
        self.prev_btn.clicked.connect(lambda: self._turn_page(-1))
        prow.addWidget(self.prev_btn)

        self.page_label = QLabel("")
        self.page_label.setObjectName("fieldLabel")
        prow.addWidget(self.page_label)

        self.next_btn = QPushButton("Next")
        self.next_btn.clicked.connect(lambda: self._turn_page(1))
        prow.addWidget(self.next_btn)
        outer.addWidget(pager)

    # ---- paging --------------------------------------------------------

    def _page_size(self):
        value = self.per_page.currentData()
        return None if value in (None, 0) else int(value)

    def _page_count(self):
        size = self._page_size()
        if not size:
            return 1
        return max(1, (len(self._visible) + size - 1) // size)

    def _pick_page_size(self, _i):
        self.engine.s.set("ui.gallery_page_size", self.per_page.currentData())
        self.engine.s.save()
        self._page = 0
        self._rebuild()

    def _turn_page(self, delta):
        self._page = max(0, min(self._page_count() - 1, self._page + delta))
        self._rebuild()

    # ---- data ----------------------------------------------------------

    def _folders(self):
        out = []
        for key, fallback in (("paths.overlay_dir", "output"),
                              ("paths.manual_dir", "")):
            try:
                if key == "paths.manual_dir" and not self.engine.s.get(key):
                    # Blank means the Desktop; the engine owns that default.
                    from avcore.engine import _desktop
                    p = _desktop()
                else:
                    p = self.engine.s.dir_for(key, fallback)
            except OSError:
                continue
            if p.exists() and p not in out:
                out.append(p)
        return out

    def _find_images(self):
        """
        Every image worth showing, newest first, listed once each.

        No cap. There used to be a hard limit of 60 here, unrelated to
        anything the user had set, so with "keep last" above that the
        extra images - favourites among them - simply could not be
        reached. How many appear at once is a paging question, not a
        question of which images exist.

        A manual image exists twice on disk: where it was saved, and as a
        mirror in the overlay folder so the web server can serve it
        without exposing the whole save directory. Both are real files,
        but they are one picture, so the gallery lists them once.

        The saved copy wins, because the mirror is subject to pruning -
        showing the mirror would make an entry disappear from the gallery
        while the user still has the file.
        """
        folders = self._folders()
        overlay = folders[0] if folders else None

        found = {}
        for folder in folders:
            is_mirror = overlay is not None and folder == overlay
            for f in folder.iterdir():
                if f.suffix.lower() not in EXTS or f.name.startswith("_"):
                    continue
                existing = found.get(f.name)
                if existing is None or (existing[1] and not is_mirror):
                    found[f.name] = (f, is_mirror)

        images = [entry[0] for entry in found.values()]
        images.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return images

    def refresh(self):
        """Re-read the folders, then redraw if anything changed."""
        images = self._find_images()

        # Rebuilding only when the set actually changed keeps this cheap
        # enough to call on every engine update.
        if images == self._all:
            return
        self._all = images
        self._apply_filters()
        self._page = min(self._page, self._page_count() - 1)
        self._rebuild()

    def _rebuild(self):
        """Draw the current page of whatever the filters allow."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        size = self._page_size()
        if size:
            start = self._page * size
            page = self._visible[start:start + size]
        else:
            start, page = 0, self._visible

        cols = self._columns()
        self._cols = cols
        catalog = getattr(self.engine, "catalog", None)
        for i, path in enumerate(page):
            entry = catalog.lookup(path) if catalog is not None else None
            self.grid.addWidget(
                Thumb(path, self._push, entry, on_changed=self._rebuild_all,
                      engine=self.engine, on_expand=self._open_viewer,
                     on_animate=self._animate,
                      on_pick=self._pick, chosen=self._chosen),
                i // cols, i % cols)

        total = len(self._visible)
        known = 0
        if catalog is not None:
            known = sum(1 for p in self._visible if catalog.lookup(p))

        if self.filters.active():
            count = f"{total} of {len(self._all)} images"
        else:
            count = (f"{total} image{'s' if total != 1 else ''}"
                     if total else "No images yet")
        if total and known < total:
            # Say why some have no caption, rather than leaving it to be
            # discovered by hovering over them.
            count += f"   ({total - known} without a recorded prompt)"
        if size and total > size:
            count += f"   showing {start + 1}-{start + len(page)}"
        self.count.setText(count)

        if not total and self.filters.active():
            self.empty.setText("Nothing matches those filters.")
        else:
            self.empty.setText(
                "Images will appear here once something has been generated.")
        self.empty.setVisible(not total)

        pages = self._page_count()
        self.page_label.setText(f"Page {self._page + 1} of {pages}"
                                if pages > 1 else "")
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < pages - 1)
        self.prev_btn.setVisible(pages > 1)
        self.next_btn.setVisible(pages > 1)
        self._reload_model_filter()
        self._sync_purge()
        viewer = getattr(self, "viewer", None)
        if viewer is not None and viewer.isVisible():
            viewer.setGeometry(self._viewer_bounds())

    def _rebuild_all(self):
        """Force a full re-read, after something changed on disk."""
        self._all = []
        self.refresh()

    def _columns(self):
        """
        How many thumbnails fit across right now.

        Fixed columns either waste most of a maximised window or overflow
        a narrow one, and this panel is the same width as whatever layout
        it happens to be in.
        """
        width = self.width() - 40          # grid margins
        step = THUMB.width() + 12          # thumbnail plus spacing
        return max(1, width // step)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._columns() != self._cols:
            # Force a rebuild; refresh() skips when the file list is
            # unchanged, which it is here.
            self._rebuild()

    def _push(self, path):
        self.engine.publish_overlay(path, source="manual")

    def _open_folder(self):
        folders = self._folders()
        if folders:
            os.startfile(str(folders[0]))

    def on_state(self, snapshot):
        # Only rescan when the image count moved; refresh() no-ops if the
        # file list is unchanged anyway.
        if snapshot.get("generated") != getattr(self, "_last_count", None):
            self._last_count = snapshot.get("generated")
            self.refresh()

    # ---- filtering -----------------------------------------------------

    def _filter_bar(self):
        """
        The row of filters above the grid.

        Text boxes react as you type rather than needing Enter, since the
        list is already in memory and narrowing it is instant.
        """
        bar = QWidget()
        bar.setObjectName("filterBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 8, 12, 8)
        row.setSpacing(8)

        self.date_pick = QComboBox()
        for label, value in DATE_RANGES:
            self.date_pick.addItem(label, value)
        self.date_pick.currentIndexChanged.connect(self._read_filters)
        row.addWidget(self.date_pick)

        self.name_box = QLineEdit()
        self.name_box.setPlaceholderText("Name contains")
        self.name_box.setClearButtonEnabled(True)
        self.name_box.textChanged.connect(self._read_filters)
        row.addWidget(self.name_box, 1)

        self.prompt_box = QLineEdit()
        self.prompt_box.setPlaceholderText("Prompt contains")
        self.prompt_box.setClearButtonEnabled(True)
        self.prompt_box.textChanged.connect(self._read_filters)
        row.addWidget(self.prompt_box, 2)

        self.source_pick = QComboBox()
        for label, value in SOURCES:
            self.source_pick.addItem(label, value)
        self.source_pick.currentIndexChanged.connect(self._read_filters)
        row.addWidget(self.source_pick)

        self.kind_pick = QComboBox()
        self.kind_pick.addItem("Any type", "any")
        # "Video" rather than WEBM: the format is an implementation
        # detail, and someone looking for their clips is not thinking
        # about containers.
        self.kind_pick.addItem("Video", "video")
        # Cut-outs, which are worth finding as a group: they are the
        # ones that can go on a stream without covering it.
        self.kind_pick.addItem("Transparent", "transparent")
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            if ext in VIDEO_EXTS:
                continue      # covered by the Video entry above
            self.kind_pick.addItem(ext.lstrip(".").upper(), ext)
        self.kind_pick.currentIndexChanged.connect(self._read_filters)
        row.addWidget(self.kind_pick)

        # Three states rather than two: left click shows only favourites,
        # right click hides them, and either button returns it to neutral
        # from the state it put it in.
        self.fav_only = TriStateFilter("Favourites")
        self.fav_only.state_changed.connect(lambda _s: self._read_filters())
        row.addWidget(self.fav_only)

        self.censor_only = TriStateFilter("Censored", noun="censored images")
        self.censor_only.state_changed.connect(
            lambda _s: self._read_filters())
        row.addWidget(self.censor_only)

        # Filled from the pictures on hand rather than from what is
        # installed: someone may have deleted a model, or be looking at
        # images made on another machine, and the question here is what
        # made these.
        self.model_filter = QComboBox()
        self.model_filter.setMinimumWidth(150)
        self.model_filter.setToolTip("Show only images made by one model")
        self.model_filter.addItem("Any model", "")
        self.model_filter.currentIndexChanged.connect(
            lambda _i: self._read_filters())
        self.model_filter.hide()
        row.addWidget(self.model_filter)

        self.clear_filters_btn = QPushButton("Clear")
        self.clear_filters_btn.setToolTip("Remove all filters")
        self.clear_filters_btn.clicked.connect(self._clear_filters)
        self.clear_filters_btn.hide()
        row.addWidget(self.clear_filters_btn)
        return bar

    def _reload_model_filter(self):
        """
        Offer the models this gallery has actually seen.

        Keeps the current choice if it is still represented: a filter
        that reset itself whenever a picture arrived would be maddening.
        Hidden entirely until there is more than one thing to choose
        between, since a picker with a single option is just clutter.
        """
        catalog = getattr(self.engine, "catalog", None)
        if catalog is None or not hasattr(catalog, "models_used"):
            return
        wanted = self.model_filter.currentData() or ""
        self.model_filter.blockSignals(True)
        self.model_filter.clear()
        self.model_filter.addItem("Any model", "")
        for key, label in catalog.models_used():
            self.model_filter.addItem(label, key)
        index = self.model_filter.findData(wanted)
        self.model_filter.setCurrentIndex(index if index >= 0 else 0)
        self.model_filter.blockSignals(False)
        self.model_filter.setVisible(self.model_filter.count() > 2)

    def _read_filters(self, *_args):
        self.filters.date = self.date_pick.currentData()
        self.filters.name = self.name_box.text()
        self.filters.prompt = self.prompt_box.text()
        self.filters.source = self.source_pick.currentData() or "any"
        self.filters.kind = self.kind_pick.currentData() or "any"
        self.filters.model = self.model_filter.currentData() or ""
        # neutral / include / exclude maps to any / only / hide.
        states = {'neutral': 'any', 'include': 'only', 'exclude': 'hide'}
        self.filters.favourites = states[self.fav_only.state()]
        self.filters.censored = states[self.censor_only.state()]
        self.clear_filters_btn.setVisible(self.filters.active())
        # Narrowing the list can leave the current page past the end.
        self._page = 0
        self._apply_filters()
        self._rebuild()

    def _clear_filters(self):
        for widget in (self.date_pick, self.source_pick, self.kind_pick):
            widget.blockSignals(True)
            widget.setCurrentIndex(0)
            widget.blockSignals(False)
        for widget in (self.name_box, self.prompt_box):
            widget.blockSignals(True)
            widget.clear()
            widget.blockSignals(False)
        for toggle in (self.fav_only, self.censor_only):
            toggle.blockSignals(True)
            toggle.set_state(TriStateFilter.NEUTRAL)
            toggle.blockSignals(False)
        self.model_filter.blockSignals(True)
        self.model_filter.setCurrentIndex(0)
        self.model_filter.blockSignals(False)
        self._read_filters()

    def _apply_filters(self):
        """Narrow the full list down to what the filters allow."""
        catalog = getattr(self.engine, "catalog", None)
        if not self.filters.active():
            self._visible = list(self._all)
            return
        favourites = catalog.favourites() if catalog is not None else set()
        hidden = catalog.censored() if catalog is not None else set()
        keep = []
        for path in self._all:
            entry = catalog.lookup(path) if catalog is not None else None
            if self.filters.matches(path, entry, path.name in favourites,
                                    path.name in hidden):
                keep.append(path)
        self._visible = keep


    def _viewer_bounds(self):
        """The gallery's own area, expressed in the window."""
        window = self.window()
        return QRect(self.mapTo(window, QPoint(0, 0)), self.size())

    def _pick_bar(self):
        """
        The row of things that can be done to a selection.

        Hidden until something is chosen, so the gallery looks exactly
        as it did before for anybody who never uses this.
        """
        bar = QWidget()
        bar.setObjectName("pickBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 2, 0, 6)
        row.setSpacing(8)

        self.pick_count = QLabel("")
        row.addWidget(self.pick_count)

        all_btn = QPushButton("Select all")
        all_btn.setToolTip("Choose everything the filters are showing")
        all_btn.clicked.connect(self._pick_all)
        row.addWidget(all_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._pick_none)
        row.addWidget(clear_btn)

        row.addStretch(1)

        self.pick_fav = QPushButton("Favourite")
        self.pick_fav.clicked.connect(self._bulk_favourite)
        row.addWidget(self.pick_fav)

        self.pick_censor = QPushButton("Censor")
        self.pick_censor.clicked.connect(self._bulk_censor)
        row.addWidget(self.pick_censor)

        self.pick_delete = QPushButton("Delete")
        self.pick_delete.setObjectName("denyButton")
        self.pick_delete.clicked.connect(self._bulk_delete)
        row.addWidget(self.pick_delete)

        bar.hide()
        return bar

    def _pick(self, path, on):
        """
        Remember a choice, by path rather than by widget.

        The grid is rebuilt whenever a filter changes or a page turns.
        """
        if on:
            self._chosen.add(Path(path))
        else:
            self._chosen.discard(Path(path))
        self._sync_picks()

    def _sync_picks(self):
        """Show what is chosen, and what can be done with it."""
        bar = getattr(self, "pick_bar", None)
        if bar is None:
            return

        # Only what the filters are showing counts: something hidden
        # should not stay quietly selected for a Delete nobody can see.
        self._chosen &= set(self._visible)
        count = len(self._chosen)
        bar.setVisible(bool(count))
        if not count:
            return

        self.pick_count.setText(
            f"{count} chosen" if count > 1 else "1 chosen")

        catalog = getattr(self.engine, "catalog", None)
        if catalog is None:
            return
        # The buttons say which way they will go rather than "toggle":
        # a mixed selection becomes all-on, which is the predictable
        # reading of pressing Favourite.
        favoured = sum(1 for p in self._chosen
                       if (catalog.lookup(p) or {}).get("favourite"))
        censored = sum(1 for p in self._chosen
                       if (catalog.lookup(p) or {}).get("censored"))
        self.pick_fav.setText(
            "Unfavourite" if favoured == count else "Favourite")
        self.pick_censor.setText(
            "Uncensor" if censored == count else "Censor")

    def _pick_all(self):
        """Choose everything the filters are currently showing."""
        self._chosen = set(self._visible)
        self._rebuild()
        self._sync_picks()

    def _pick_none(self):
        self._chosen.clear()
        self._rebuild()
        self._sync_picks()

    def _bulk_favourite(self):
        catalog = getattr(self.engine, "catalog", None)
        if catalog is None or not self._chosen:
            return
        wanted = self.pick_fav.text() == "Favourite"
        for path in sorted(self._chosen):
            catalog.set_favourite(path, wanted)
        self._say(f"{len(self._chosen)} "
                  f"{'favourited' if wanted else 'unfavourited'}.")
        self._rebuild_all()

    def _bulk_censor(self):
        catalog = getattr(self.engine, "catalog", None)
        if catalog is None or not self._chosen:
            return
        wanted = self.pick_censor.text() == "Censor"
        for path in sorted(self._chosen):
            catalog.set_censored(path, wanted)
        self._say(f"{len(self._chosen)} "
                  f"{'censored' if wanted else 'uncensored'}.")
        self._rebuild_all()

    def _bulk_delete(self):
        """
        Delete everything chosen, having asked once.

        Once for the batch rather than once per picture: twenty dialogs
        is not twenty times the safety, it is twenty times the clicking,
        and people stop reading by the third.
        """
        from .dialogs import ConfirmBulkDelete

        if not self._chosen:
            return
        if ConfirmBulkDelete(len(self._chosen),
                             self).exec() != QDialog.Accepted:
            return

        gone = failed = 0
        for path in sorted(self._chosen):
            try:
                Path(path).unlink()
                gone += 1
            except OSError:
                failed += 1
        self._chosen.clear()
        self._say(f"Deleted {gone}." if not failed
                  else f"Deleted {gone}; {failed} could not be removed.")
        self._rebuild_all()

    def _say(self, message):
        """
        A passing note, shown where the image count lives.

        Put back by the next rebuild, which is the right lifetime for
        "your clip is being made" - it stops mattering the moment the
        gallery refreshes with the clip in it.
        """
        label = getattr(self, "count", None)
        if label is not None:
            label.setText(message)

    def _animate(self, source):
        """
        Turn a picture into a clip, having asked first.

        Asked because it is two minutes of the graphics card and a file
        that takes up space - not the sort of thing to start from a
        stray menu click.
        """
        from .dialogs import ConfirmAnimate

        if getattr(self, "_animator", None) is not None \
                and self._animator.isRunning():
            self._say(
                "One clip is already being made. They take a couple of "
                "minutes and only one runs at a time.")
            return

        from avcore.video import plan, shape_for

        picture = QPixmap(str(source))
        shape = shape_for(picture.width(), picture.height())

        dialog = ConfirmAnimate(Path(source).name, shape,
                                self.engine.s, self)
        if dialog.exec() != QDialog.Accepted:
            return

        frames, shape = dialog.choice()
        _w, _h, _seconds, estimate = plan(frames, shape, self.engine.s)
        self.engine.s.set("video.frames", frames)
        self.engine.s.set("video.shape", shape)
        self.engine.s.save()

        self._start_clip_progress(Path(source).name, estimate)
        self._animator = AnimateWorker(self.engine, source, self)
        self._animator.done.connect(self._clip_done)
        self._animator.failed.connect(self._clip_failed)
        self._animator.start()

    def _start_clip_progress(self, name, estimate):
        """
        A bar at the top, counting down.

        Three minutes with nothing moving is indistinguishable from a
        hang. There is no per-step progress to be had over ComfyUI's
        HTTP interface, so this counts against the measured estimate and
        says so - an honest guess beats a bar that pretends to know.
        """
        self.clip_bar.setRange(0, max(1, int(estimate)))
        self.clip_bar.setValue(0)
        self.clip_bar.show()
        self.clip_stop.show()
        self._clip_name = name
        self._clip_started = time.time()
        self._clip_estimate = max(1, int(estimate))
        self._clip_timer.start()
        self._tick_clip()

    def _tick_clip(self):
        elapsed = int(time.time() - self._clip_started)
        left = self._clip_estimate - elapsed
        self.clip_bar.setValue(min(elapsed, self._clip_estimate))
        if left > 0:
            minutes, rest = divmod(left, 60)
            when = (f"{minutes}m {rest:02d}s" if minutes
                    else f"{rest} seconds")
            self.clip_bar.setFormat(
                f"Animating {self._clip_name} - about {when} left")
        else:
            # Past the estimate. Saying "any moment now" is friendlier
            # than a full bar that sits there, and truer than a
            # percentage that cannot go past 100.
            self.clip_bar.setFormat(
                f"Animating {self._clip_name} - any moment now")

    def _stop_clip_progress(self):
        self._clip_timer.stop()
        self.clip_bar.hide()
        self.clip_stop.hide()

    def _cancel_clip(self):
        self._say("Stopping the clip...")
        cancel = getattr(self.engine, "cancel_video", None)
        if cancel is not None:
            cancel()

    def _clip_done(self, clip):
        self._stop_clip_progress()
        if clip is None:
            self._say("The clip was stopped.")
            return
        self._say(
            f"{Path(clip).name} is ready. Filter by Video to find it.")
        self._rebuild_all()

    def _clip_failed(self, message):
        self._stop_clip_progress()
        self._say(message)

    def _open_viewer(self, path, thumb_widget):
        """
        Expand an image, growing out of the thumbnail that asked.

        The list handed over is what is currently visible, not everything
        on disk: arrowing through a filtered gallery should walk the
        images the filter chose, or the filter would mean nothing as soon
        as the picture got bigger.
        """
        if getattr(self, "viewer", None) is None:
            # Parented to the window rather than to the gallery, so it
            # can sit above the decoration layer - which is itself a
            # child of the window. A child of the gallery can never rise
            # above one of the window's own children, whatever it does
            # with raise_.
            self.viewer = ImageViewer(self.window())
            self.viewer.closed.connect(self._viewer_closed)

        shown = list(self._visible)
        if path not in shown:
            shown = [path]
        index = shown.index(path)

        # Where the thumbnail sits, in the window's coordinates now that
        # the viewer lives there.
        window = self.window()
        top_left = thumb_widget.mapTo(window, QPoint(0, 0))
        home = QRect(top_left, thumb_widget.size())

        catalog = getattr(self.engine, "catalog", None)
        censored = catalog.censored() if catalog is not None else set()
        captions = {}
        if catalog is not None:
            for item in shown:
                entry = catalog.lookup(item)
                if entry and entry.get("prompt"):
                    # The expanded view has room for the model, and it
                    # is the place someone studies an image closely
                    # enough to wonder what made it.
                    made_by = model_label(entry.get("backend"),
                                          entry.get("model"), short=True)
                    captions[item.name] = (
                        f"{entry['prompt']}   -   {made_by}"
                        if made_by else entry["prompt"])

        # A clip cannot be shown by the image viewer, so it gets the
        # player instead. Same place on screen, same way out.
        if Path(path).suffix.lower() in VIDEO_EXTS:
            self._open_clip(path)
            return

        self.viewer.setGeometry(self._viewer_bounds())
        self.viewer.open_at(shown, index, home, censored, captions)
        # Above the decoration, and above everything else the window
        # stacks over its panels.
        self.viewer.raise_()

    def _open_clip(self, path):
        """
        Play a clip over the gallery.

        Built on demand: most people never make one, and Qt Multimedia
        is heavy enough that loading it for everybody would be rude.
        """
        from .video_player import VideoPlayer

        if getattr(self, "clip_view", None) is None:
            holder = QWidget(self.window())
            holder.setObjectName("viewer")
            holder.setAttribute(Qt.WA_StyledBackground, True)
            column = QVBoxLayout(holder)
            column.setContentsMargins(24, 20, 24, 20)
            column.setSpacing(10)

            self.clip_player = VideoPlayer()
            column.addWidget(self.clip_player, 1)

            row = QHBoxLayout()
            self.clip_name = QLabel("")
            self.clip_name.setObjectName("fieldLabel")
            row.addWidget(self.clip_name, 1)
            close = QPushButton("Close")
            close.clicked.connect(self._close_clip)
            row.addWidget(close)
            column.addLayout(row)

            self.clip_view = holder

        self.clip_name.setText(Path(path).name)
        self.clip_view.setGeometry(self._viewer_bounds())
        self.clip_view.show()
        self.clip_view.raise_()
        self.clip_player.play_file(path)

    def _close_clip(self):
        """
        Put the player away, and let go of the file.

        Stopping matters beyond tidiness: Windows holds a lock on a file
        being played, and deleting the clip afterwards would fail with a
        permission error that reads like a bug.
        """
        player = getattr(self, "clip_player", None)
        if player is not None:
            player.stop()
        view = getattr(self, "clip_view", None)
        if view is not None:
            view.hide()

    def _viewer_closed(self):
        # The censor marks may have changed while it was open.
        self._rebuild_all()

    def _purge(self):
        """
        Empty the gallery of everything not favourited.

        The count is taken from the engine rather than from what is on
        screen: filters and paging mean the grid often shows a fraction
        of what exists, and purging more than the user can see would be a
        nasty surprise.
        """
        engine = self.engine
        if engine is None or not hasattr(engine, "purgeable"):
            return
        doomed = engine.purgeable()
        if not doomed:
            return

        catalog = getattr(engine, "catalog", None)
        favourites = len(catalog.favourites()) if catalog else 0
        if ConfirmPurge(len(doomed), favourites,
                        self).exec() != QDialog.Accepted:
            return

        removed, failed = engine.purge_images()
        self._rebuild_all()
        if failed:
            self.count.setText(
                f"Removed {removed}; {failed} could not be deleted")

    def _sync_purge(self):
        """Nothing to purge means nothing to press."""
        button = getattr(self, "purge_btn", None)
        if button is None:
            return
        engine = self.engine
        count = len(engine.purgeable()) if hasattr(engine, "purgeable") else 0
        button.setEnabled(count > 0)
        button.setToolTip(
            f"Delete {count} image{'s' if count != 1 else ''} that are not "
            f"favourited" if count
            else "Nothing to purge - every image is a favourite")
