"""
The main window.

The three layouts differ only in how the same four panel objects are
arranged. Switching reparents them; it never rebuilds them, so a render in
flight and everything on screen survives the change.
"""

import webbrowser

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QSplitter, QStackedWidget, QVBoxLayout,
    QWidget,
)

from avcore.version import __version__

from . import theme
from .assets import asset, load_pixmap
from .bridge import EngineBridge
from .about_panel import AboutPanel
from .customize_panel import CustomizePanel
from .quick_settings import QuickSettings
from .version_label import VersionLabel
from .decorations import DecorationLayer
from .gallery_panel import GalleryPanel
from .overlays import ChangeBar, SubNavItem, Vignette
from .panels import LivePanel
from .prompt_panel import PromptPanel
from .settings_panel import SettingsPanel
from .setup_panel import SetupPanel

# Which nav entries each layout shows, and which panels sit on each page.
# "split" puts live and prompt on one page, so it has one fewer entry.
# Short names for the side bar picker. The long descriptions belong in
# Settings, where there is room to read them; here they would not fit
# and nobody browsing layouts needs the explanation twice.
LAYOUT_CHOICES = [
    ("hybrid", "Hybrid"),
    ("sidebar", "Panels"),
    ("split", "Split"),
]

LAYOUTS = {
    "hybrid": {
        "nav": [("live", "Live"), ("prompt", "Prompt"),
                ("gallery", "Gallery"), ("setup", "Setup"),
                ("settings", "Settings")],
        "strip": True,
        "prompt_preview": True,
        "min_width": 800,
    },
    "sidebar": {
        "nav": [("live", "Live"), ("prompt", "Prompt"),
                ("gallery", "Gallery"), ("setup", "Setup"),
                ("settings", "Settings")],
        "strip": False,
        "prompt_preview": True,
        "min_width": 800,
    },
    "split": {
        "nav": [("live", "Live and prompt"), ("gallery", "Gallery"),
                ("setup", "Setup"), ("settings", "Settings")],
        "strip": False,
        "prompt_preview": False,
        "min_width": 1100,
    },
}


class MainWindow(QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setWindowTitle("WispWasp")

        self.bridge = EngineBridge(engine, self)
        self.bridge.changed.connect(self._on_state)
        self.bridge.level.connect(self._on_level)

        # Built once, reused by every layout.
        self.live = LivePanel(engine)
        self.prompt = PromptPanel(engine)
        self.gallery = GalleryPanel(engine)
        self.setup = SetupPanel(engine)
        self.settings = SettingsPanel(
            engine, on_layout_change=self.set_layout)
        self.customize = CustomizePanel(engine)
        self.customize.palette_changed.connect(self._repaint_theme)
        self.about = AboutPanel(engine)
        self._panels = {
            "live": self.live, "prompt": self.prompt,
            "gallery": self.gallery, "setup": self.setup,
            "settings": self.settings, "customize": self.customize,
            "about": self.about,
        }

        self.layout_name = None
        self._splitter_sizes = None
        self.set_layout(engine.s.get("ui.layout", "hybrid"), save=False)
        self._wire_quick()
        self._wire_hotkeys()
        self._restore_geometry()

        # Overlays live on the window, so they float above whatever is
        # showing and survive a layout switch.
        self.decor = DecorationLayer(self)
        self.vignette = Vignette(self)
        self.change_bar = ChangeBar(self)
        self.change_bar.applied.connect(self._apply_settings)
        self.change_bar.cancelled.connect(self._cancel_settings)
        self.settings.dirty_changed.connect(self._on_dirty)
        self.customize.decor_changed.connect(self._apply_decor)

        from .sounds import ThemeSounds
        from avcore.config import DATA_DIR
        self.sounds = ThemeSounds(DATA_DIR / "sounds")
        self._apply_decor()

        # A fresh install has no ComfyUI and no model, so open on Setup
        # rather than on a Live panel that could only show errors.
        if not self.setup.report.get("ready"):
            self.show_panel("setup")

    # ---- layout --------------------------------------------------------

    def set_layout(self, name, save=True):
        if name not in LAYOUTS:
            name = "hybrid"
        if name == self.layout_name:
            return
        spec = LAYOUTS[name]

        # Detach the panels before the old shell is destroyed. Qt's C++
        # side owns its children, so deleting the container while they are
        # still attached would take them down with it.
        if self.layout_name is not None:
            self._remember_splitter()
            for panel in self._panels.values():
                panel.setParent(None)
            old = self.takeCentralWidget()
            if old is not None:
                old.deleteLater()

        self.layout_name = name
        self.live.set_strip_visible(spec["strip"])
        self.prompt.set_preview_visible(spec["prompt_preview"])
        self.setMinimumSize(spec["min_width"], 560)
        self.setCentralWidget(self._shell(spec))

        # The panels missed any updates while detached, so hand them the
        # current state rather than waiting for the next engine event.
        self._on_state(self.engine.snapshot())

        # The new central widget is stacked above the overlays, and the
        # decoration's cached geometry points at panels that no longer
        # exist. Both have to be put right or the theme half disappears.
        self._lift_overlays()

        if save:
            self.engine.s.set("ui.layout", name)
            self.engine.s.save()

    def _lift_overlays(self):
        """Put the overlays back on top, and re-measure the decoration."""
        decor = getattr(self, "decor", None)
        if decor is not None:
            decor.refresh()
        for overlay in (getattr(self, "vignette", None),
                        getattr(self, "change_bar", None)):
            if overlay is not None:
                overlay.raise_()
        bar = getattr(self, "change_bar", None)
        if bar is not None:
            bar.reposition()

        # The expanded viewer sits above the decoration, so anything
        # that re-lifts the decoration has to lift it again after.
        self._lift_viewer()

    def _lift_viewer(self):
        """Put the expanded image back on top, if one is open."""
        gallery = getattr(self, "gallery", None)
        viewer = getattr(gallery, "viewer", None) if gallery else None
        if viewer is not None and viewer.isVisible():
            viewer.setGeometry(gallery._viewer_bounds())
            viewer.raise_()

    def _remember_splitter(self):
        splitter = getattr(self, "splitter", None)
        if splitter is not None:
            try:
                self._splitter_sizes = splitter.sizes()
            except RuntimeError:
                pass
        self.splitter = None

    def _shell(self, spec):
        """Sidebar plus a stack of pages, per the layout spec."""
        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._sidebar(spec["nav"]))

        self.stack = QStackedWidget()
        self.splitter = None
        self._page_keys = []

        for key, _label in spec["nav"]:
            if key == "live" and self.layout_name == "split":
                self.stack.addWidget(self._live_and_prompt())
            else:
                self.stack.addWidget(self._panels[key])
            self._page_keys.append(key)
            # Customize has no nav entry of its own - it unrolls under
            # Settings - but it still needs a page to live on.
            if key == "settings":
                self.stack.addWidget(self._panels["customize"])
                self._page_keys.append("customize")
                self.stack.addWidget(self._panels["about"])
                self._page_keys.append("about")

        row.addWidget(self.stack, 1)
        return root

    def _live_and_prompt(self):
        """The split layout: live view and prompt side by side, draggable."""
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.live)
        self.splitter.addWidget(self.prompt)
        # Detaching a panel with setParent(None) hides it, and a splitter
        # will not show a hidden child it adopts - it just allocates it no
        # space. A stacked widget manages visibility itself, so this is
        # only needed here.
        self.live.show()
        self.prompt.show()
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        if self._splitter_sizes and len(self._splitter_sizes) == 2:
            target = list(self._splitter_sizes)
        else:
            target = [640, 420]

        # Sizes have to be applied after Qt has laid the splitter out.
        # Setting them now, while it is still zero-width and unparented,
        # normalises them against no space at all and silently discards
        # them - which is why the proportions came out wrong.
        splitter = self.splitter

        def apply_sizes():
            try:
                if splitter.width() > 0:
                    splitter.setSizes(target)
            except RuntimeError:
                pass   # layout changed again before this ran

        QTimer.singleShot(0, apply_sizes)
        return self.splitter

    def _sidebar(self, nav):
        bar = QWidget()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(168)
        col = QVBoxLayout(bar)
        col.setContentsMargins(0, 0, 0, 12)
        col.setSpacing(0)

        # The logo sits above the nav. It is the one thing visible in every
        # layout, and the top of the sidebar was empty space anyway.
        logo = QLabel()
        logo.setObjectName("brandMark")
        pm = load_pixmap("wispwasp_logo.png", width=132,
                         dpr=self.devicePixelRatioF())
        if pm is not None:
            logo.setPixmap(pm)
        else:
            # Missing artwork should not leave a blank corner.
            logo.setText("WispWasp")
        logo.setAlignment(Qt.AlignCenter)
        # Clicking the mark is the one hidden thing in the app. It costs
        # nothing, does nothing, and is only ever found on purpose.
        logo.setCursor(Qt.PointingHandCursor)
        logo.mousePressEvent = self._logo_pressed
        logo.setContentsMargins(0, 16, 0, 14)
        col.addWidget(logo)

        line = QFrame()
        line.setObjectName("brandRule")
        line.setFixedHeight(1)
        col.addWidget(line)
        col.addSpacing(10)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, (_key, label) in enumerate(nav):
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            # Routed through show_panel so the unapplied-changes block
            # applies to the nav buttons as well as to code calling it.
            btn.clicked.connect(
                lambda _c, k=nav[i][0]: self.show_panel(k))
            self.nav_group.addButton(btn, i)
            col.addWidget(btn)

            # Customize unrolls beneath Settings rather than sitting in
            # the list permanently: it belongs to that page, and a nav
            # that grows only when you are there says so.
            if nav[i][0] == "settings":
                self.customize_nav = SubNavItem("Customize")
                self.customize_nav.clicked.connect(
                    lambda: self.show_panel("customize"))
                col.addWidget(self.customize_nav)

                # About keeps Customize company rather than taking a
                # place in the main list: it belongs to Settings, and it
                # is not somewhere anybody goes twice in a session.
                self.about_nav = SubNavItem("About")
                self.about_nav.clicked.connect(
                    lambda: self.show_panel("about"))
                col.addWidget(self.about_nav)

        # The quick settings themselves now sit above the prompt boxes,
        # where the eye already is when deciding what to make. Only the
        # layout picker stays here: it is about the window rather than
        # about the next image.
        self.layout_label = QLabel("Layout")
        self.layout_label.setObjectName("sectionLabel")
        col.addWidget(self.layout_label)
        self.layout_pick = QComboBox()
        for key, name in LAYOUT_CHOICES:
            self.layout_pick.addItem(name, key)
        self.layout_pick.setMaximumWidth(140)
        self.layout_pick.setToolTip("How the Live page is arranged")
        self.layout_pick.currentIndexChanged.connect(self._quick_layout)
        col.addWidget(self.layout_pick)

        col.addStretch(1)

        label = QLabel("Overlay for OBS")
        label.setObjectName("sectionLabel")
        col.addWidget(label)

        url_btn = QPushButton("Copy browser URL")
        url_btn.setObjectName("navButton")
        url_btn.setToolTip("For an OBS Browser Source")
        url_btn.clicked.connect(self._copy_url)
        col.addWidget(url_btn)

        win_btn = QPushButton("Open overlay window")
        win_btn.setObjectName("navButton")
        win_btn.setToolTip("For OBS Window Capture")
        win_btn.clicked.connect(self._open_overlay)
        col.addWidget(win_btn)

        self.url_note = QLabel("")
        self.url_note.setObjectName("sectionLabel")
        self.url_note.setWordWrap(True)
        col.addWidget(self.url_note)

        # Quietest thing in the window, in the corner where a version
        # belongs. It matters when somebody reports a problem: "it does
        # this" is much easier to act on when the build is known.
        self.version_label = VersionLabel(f"v{__version__}")
        self.version_label.setObjectName("versionLabel")
        self.version_label.setToolTip(
            "The version of WispWasp you are running")
        col.addWidget(self.version_label)
        return bar

    # ---- overlay -------------------------------------------------------

    def _overlay_url(self, bare):
        port = int(self.engine.s.get("server.port", 8420))
        suffix = "?bare=1" if bare else ""
        return f"http://127.0.0.1:{port}/overlay.html{suffix}"

    def _copy_url(self):
        # The version rides along in the URL. OBS keys its cache on the
        # address, so a page that changed shape between releases - the
        # day it learned to be transparent, for instance - is fetched
        # fresh rather than carried over from the last one.
        url = self._overlay_url(bare=True)
        joiner = "&" if "?" in url else "?"
        QGuiApplication.clipboard().setText(f"{url}{joiner}v={__version__}")
        server = getattr(self.engine, "server", None)
        if server is not None and not server.is_running():
            # Handing over a URL that cannot answer would look like an OBS
            # problem, so say what is actually wrong.
            self.url_note.setText(
                server.error or "Copied, but the overlay server is not "
                                "running.")
        else:
            self.url_note.setText("Copied. Paste into a Browser Source.")

    def _open_overlay(self):
        server = getattr(self.engine, "server", None)
        if server is not None and not server.is_running():
            self.url_note.setText(
                server.error or "The overlay server is not running.")
            return
        webbrowser.open(self._overlay_url(bare=False))
        self.url_note.setText("Capture this browser window in OBS.")

    # ---- unapplied settings ---------------------------------------------

    def _on_dirty(self, dirty):
        """The bar follows the panel: up when there is something to decide."""
        if dirty:
            self.change_bar.slide_in()
        elif not self.change_bar.is_confirming():
            # While confirming, the bar takes itself away after its glow.
            # Pulling it down here would cut that short, and applying is
            # exactly when the dirty flag clears.
            self.change_bar.slide_out()

    def _apply_settings(self):
        self.settings.apply_pending()

    def _cancel_settings(self):
        self.settings.cancel_pending()

    def _block_navigation(self):
        """
        True when the user must deal with the change bar first.

        Refusing silently would just look broken, so the window border
        flashes and the bar trembles - enough to point at the thing that
        needs answering without an interrupting dialog.
        """
        if not self.settings.is_dirty():
            return False
        self.vignette.flash(theme.FAVOURITE)
        self.change_bar.shake()
        return True

    def _apply_decor(self):
        """Push the chosen theme and its two toggles onto the layer."""
        s = self.engine.s
        # The theme may reshape controls, which is a stylesheet job, so
        # the whole sheet is rebuilt rather than only the layer redrawn.
        app = QApplication.instance()
        if app is not None:
            theme.apply_to(app, s)
        self.decor.setGeometry(self.rect())
        self.decor.set_theme(s.get("ui.decor_theme", "none"))
        self.decor.set_animated(bool(s.get("ui.decor_animate", True)))
        self.decor.set_enhanced(
            bool(s.get("ui.decor_enhanced", False)))
        sounds = getattr(self, "sounds", None)
        if sounds is not None:
            sounds.set_theme(s.get("ui.decor_theme", "none"))
            sounds.set_enabled(
                bool(s.get("ui.decor_sounds", False))
                and (s.get("ui.decor_theme", "none") or "none") != "none")
        # The decoration sits above the panels but below the change bar,
        # which must stay clickable and legible.
        self.decor.raise_()
        self.vignette.raise_()
        self.change_bar.raise_()
        self._lift_viewer()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Overlays are not in a layout, so they are placed by hand.
        if hasattr(self, "vignette"):
            self.decor.setGeometry(self.rect())
            self.vignette.setGeometry(self.rect())
            self.change_bar.reposition()
            self._lift_viewer()

    # ---- navigation ----------------------------------------------------

    def current_panel(self):
        """Which page is showing, by name."""
        index = self.stack.currentIndex()
        if 0 <= index < len(self._page_keys):
            return self._page_keys[index]
        return ""

    def _wire_hotkeys(self):
        """
        Install the shortcuts, and say what each one does.

        The handlers are looked up through the live panel rather than
        held directly, because a layout change rebuilds the panels and a
        captured reference would point at a widget that is no longer on
        screen.
        """
        from .hotkeys import Hotkeys

        self.hotkeys = Hotkeys(self, self.engine.s)
        self.hotkeys.bind("listen", self._hotkey_listen)
        self.hotkeys.bind("capture", lambda: self.engine.capture_now())
        self.hotkeys.bind("repeat", lambda: self.engine.repeat_last())
        self.hotkeys.bind("cancel", lambda: self.engine.cancel_current())
        self.hotkeys.bind("clear", lambda: self.engine.clear_overlay())
        QApplication.instance().installEventFilter(self.hotkeys)

    def _hotkey_listen(self):
        """Start or stop listening, whichever applies."""
        live = getattr(self, "live", None)
        toggle = getattr(live, "_toggle_listen", None)
        if toggle is not None:
            toggle()

    def show_panel(self, key):
        """Switch views by name. Also used by the --panel dev flag."""
        if key not in self._page_keys:
            return False
        # Leaving Settings with changes in the air would quietly lose
        # them, so it is refused until the user decides.
        if (self.stack.currentIndex() == self._page_keys.index("settings")
                and key != "settings" and self._block_navigation()):
            self._sync_nav()
            return False
        idx = self._page_keys.index(key)
        moved = idx != self.stack.currentIndex()
        self.stack.setCurrentIndex(idx)
        btn = self.nav_group.button(idx)
        if btn:
            btn.setChecked(True)
        self._sync_sub_nav(key)
        gallery = getattr(self, "gallery", None)
        viewer = getattr(gallery, "viewer", None) if gallery else None
        if key != "gallery" and viewer is not None and viewer.isVisible():
            # It covers the gallery, so leaving the gallery has to take
            # it with you rather than stranding it over another page.
            viewer.collapse()

        # The clip player covers the gallery in the same way and was
        # left behind: leaving the page stranded a video playing over
        # whatever came next.
        if key != "gallery":
            close_clip = getattr(gallery, "_close_clip", None)
            if close_clip is not None:
                close_clip()

        # A model may have been installed, or the size changed in
        # Settings, since these were last looked at.
        if key in ("live", "prompt"):
            self._sync_quick()

        if key == "gallery":
            # Re-read the folder on arrival. Images can appear while
            # another page is open, and a gallery that only updates when
            # something happens to change on screen looks stale.
            self.gallery._rebuild_all()
        if moved:
            self._page_sound()
        return True

    def _logo_pressed(self, event):
        """
        The easter egg.

        A wisp on the first click, and a flash of the accent colour if
        someone keeps going - the vignette already exists for refused
        navigation, so the reward costs nothing but a call.
        """
        if event.button() != Qt.LeftButton:
            return
        self._logo_taps = getattr(self, "_logo_taps", 0) + 1
        sounds = getattr(self, "sounds", None)
        if sounds is not None and sounds.enabled:
            sounds.play_event("egg")
        if self._logo_taps % 5 == 0 and hasattr(self, "vignette"):
            self.vignette.flash(theme.ACCENT)

    def _page_sound(self):
        """
        A rustle on changing page, and very occasionally more.

        The rustle is quiet and short because it happens constantly. The
        rare creak is the reward for using the app for a while - it turns
        up about one change in twenty-five, which is seldom enough to
        stay a surprise.
        """
        sounds = getattr(self, "sounds", None)
        if sounds is None or not sounds.enabled:
            return
        sounds.play_event("page")
        import random
        if random.random() < 0.04:
            QTimer.singleShot(320, lambda: sounds.play_event("rare"))

    def _repaint_theme(self):
        """
        Re-apply the stylesheet after a colour change.

        Widgets that paint themselves read the palette when they draw, so
        they only need telling to redraw; the rest is carried by the new
        stylesheet.
        """
        app = QApplication.instance()
        if app is not None:
            theme.apply_to(app, self.engine.s)
        for widget in self.findChildren(QWidget):
            widget.update()
        self.update()

    def _wire_quick(self):
        """
        Connect every copy of the quick settings.

        There is one per prompt box, and a change in either has to reach
        the other and the Settings page - three views of the same
        values, which must never disagree.
        """
        for panel in (getattr(self, "live", None),
                      getattr(self, "prompt", None)):
            quick = getattr(panel, "quick", None)
            if quick is not None and not getattr(quick, "_wired", False):
                quick._wired = True
                quick.changed.connect(self._quick_changed)

    def _quick_changed(self):
        """
        A quick change landed, so anything showing those values re-reads.

        The Settings page keeps its own widgets, and a value changed
        from the side bar would otherwise still read the old one there
        until the app restarted.
        """
        settings = getattr(self, "settings", None)
        adopt = getattr(settings, "_adopt_settings", None)
        if adopt is not None and not settings.is_dirty():
            adopt()
        self._sync_quick()

    def _quick_layout(self, _index):
        wanted = self.layout_pick.currentData()
        if not wanted or wanted == self.engine.s.get("ui.layout", "hybrid"):
            return
        self.engine.s.set("ui.layout", wanted)
        self.engine.s.save()
        self.set_layout(wanted)
        self._sync_quick()

    def _sync_quick(self):
        """Re-read every copy of the quick settings."""
        for panel in (getattr(self, "live", None),
                      getattr(self, "prompt", None)):
            quick = getattr(panel, "quick", None)
            if quick is not None:
                quick.refresh()
        picker = getattr(self, "layout_pick", None)
        if picker is not None:
            current = self.engine.s.get("ui.layout", "hybrid")
            index = picker.findData(current)
            if index >= 0 and index != picker.currentIndex():
                picker.blockSignals(True)
                picker.setCurrentIndex(index)
                picker.blockSignals(False)

    def _sync_sub_nav(self, key):
        """
        Show Customize while Settings or Customize is open.

        Both count as being "in settings", so stepping onto the sub-page
        does not roll the entry it was reached from back up.
        """
        nav = getattr(self, "customize_nav", None)
        if nav is None:
            return
        inside = key in ("settings", "customize", "about")
        about = getattr(self, "about_nav", None)
        if inside:
            nav.reveal()
            if about is not None:
                about.reveal()
        else:
            nav.conceal()
            if about is not None:
                about.conceal()
        nav.set_checked(key == "customize")
        if about is not None:
            about.set_checked(key == "about")
        # The parent entry stays marked while either sub-page is open.
        if key in ("customize", "about") and "settings" in self._page_keys:
            parent = self.nav_group.button(
                self._page_keys.index("settings"))
            if parent:
                parent.setChecked(True)

    def _sync_nav(self):
        """Put the nav highlight back on the page actually showing."""
        current = self.stack.currentIndex()
        btn = self.nav_group.button(current)
        if btn:
            btn.setChecked(True)

    # ---- state ---------------------------------------------------------

    def _on_state(self, snapshot):
        """Fan the snapshot out to every panel that wants it."""
        self._sound_for(snapshot)
        for panel in self._panels.values():
            handler = getattr(panel, "on_state", None)
            if handler:
                handler(snapshot)

    def _sound_for(self, snapshot):
        """
        Speak on changes, never on repeats.

        The engine publishes state many times a second, so every sound is
        tied to a transition rather than to a value - otherwise starting
        to listen would chatter for as long as it kept listening.
        """
        sounds = getattr(self, "sounds", None)
        if sounds is None or not sounds.enabled:
            self._heard_state = snapshot
            return
        was = getattr(self, "_heard_state", None) or {}

        listening = bool(snapshot.get("listening"))
        if listening != bool(was.get("listening")):
            sounds.play_event("listen_start" if listening else "listen_stop")

        cleared = bool(snapshot.get("overlay_cleared"))
        if cleared and not bool(was.get("overlay_cleared")):
            sounds.play_event("cleared")

        made = snapshot.get("generated", 0)
        if made > was.get("generated", made):
            sounds.play_event("image")

        self._heard_state = snapshot

    def _on_level(self, rms):
        """
        Audio level, straight to whoever draws a meter.

        Kept off the snapshot path deliberately - this arrives twenty times
        a second and only one panel cares.
        """
        for panel in self._panels.values():
            handler = getattr(panel, "on_level", None)
            if handler:
                handler(rms)

    # ---- geometry ------------------------------------------------------

    def _restore_geometry(self):
        qs = QSettings("WispWasp", "WispWasp")
        geo = qs.value("geometry")
        if geo:
            self.restoreGeometry(geo)
            return
        # First run: wide enough for the prompt strip to breathe without
        # taking over a monitor. Centred on the primary screen.
        self.resize(1040, 700)
        screen = QGuiApplication.primaryScreen()
        frame = self.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        self.move(frame.topLeft())

    def closeEvent(self, event):
        qs = QSettings("WispWasp", "WispWasp")
        qs.setValue("geometry", self.saveGeometry())
        self.bridge.detach()
        self.engine.shutdown()
        super().closeEvent(event)


def run(engine, panel=None):
    app = QApplication.instance() or QApplication([])
    # Built from the saved palette rather than the defaults, so a chosen
    # colour scheme is there from the first frame instead of appearing a
    # moment later.
    theme.apply_to(app, engine.s)

    # Sets the taskbar and titlebar icon. The square crop is used rather
    # than the lockup, since anything wide becomes unreadable once Windows
    # shrinks it into a corner.
    icon_file = asset("wispwasp_icon.png")
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))

    win = MainWindow(engine)
    if panel:
        win.show_panel(panel)
    win.show()
    engine.start()
    return app.exec()
