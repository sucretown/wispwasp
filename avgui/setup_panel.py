"""
First-run setup panel.

Downloads happen on a worker thread and report back through Qt signals, so
the window stays responsive through an 8 GB transfer and the Cancel button
actually works.
"""

import threading

from PySide6.QtCore import QTimer, Qt, QObject, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QRadioButton,
    QFileDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from avcore.version import __version__
from avcore import setup as avsetup
from avcore.comfy_launcher import ComfyLauncher


class SetupWorker(QObject):
    progress = Signal(dict)
    step = Signal(str, str)
    finished = Signal(bool, str)

    def __init__(self, settings, root):
        super().__init__()
        self.s = settings
        self.root = root
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        inst = avsetup.Installer(
            self.s,
            on_progress=lambda **kw: self.progress.emit(kw),
            on_step=lambda key, text: self.step.emit(key, text),
            cancel=self._cancel.is_set,
        )
        try:
            inst.run(root=self.root)
        except avsetup.SetupCancelled:
            self.finished.emit(False, "Stopped. Anything downloaded so far "
                                      "is kept, so it will carry on from "
                                      "there next time.")
            return
        except Exception as exc:
            self.finished.emit(False, str(exc))
            return
        self.finished.emit(True, "Everything is installed.")


class CheckRow(QWidget):
    """One requirement, with a status mark and a line of explanation."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 3, 0, 3)
        row.setSpacing(10)

        self.mark = QLabel("\u2022")
        self.mark.setFixedWidth(16)
        self.mark.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title)
        self.title.setMinimumWidth(150)
        self.detail = QLabel("checking...")
        self.detail.setObjectName("fieldLabel")
        self.detail.setWordWrap(True)

        row.addWidget(self.mark)
        row.addWidget(self.title)
        row.addWidget(self.detail, 1)

    def set_state(self, ok, detail):
        from . import theme
        if ok is None:
            self.mark.setText("\u2022")
            colour = theme.MUTED
        elif ok:
            self.mark.setText("\u2713")
            colour = theme.OK
        else:
            self.mark.setText("\u2715")
            colour = theme.WORKING
        self.mark.setStyleSheet(f"color: {colour}; font-weight: 600;")
        self.detail.setText(detail)


def _progress_text(done, total):
    """
    What a download bar should say while it works.

    In megabytes, whatever the size. The first ninety-five megabytes of
    a nine gigabyte file round to nought per cent, and a bar reading
    "0%  0.0 of 9.6 GB" for a minute and a half is indistinguishable
    from one that is stuck - which is exactly how it was reported.
    Megabytes tick over every second and say plainly that something is
    happening.
    """
    return (f"%p%   {done / 1_000_000:,.0f} of "
            f"{total / 1_000_000:,.0f} MB")


class ModelFetch(QThread):
    """
    Downloads the video model, resumably, off the interface thread.

    Uses the same fetcher as the model catalogue, so a part-finished
    8.9GB download is picked up where it stopped rather than started
    again - which at this size is the difference between an annoyance
    and an afternoon.
    """

    # 64-bit for the same reason as the model browser's worker: Qt's int
    # is 32 bits, and the video model is 9.6GB. Through a narrow signal
    # that arrives as 5.3GB - wrong, but believable enough to go
    # unreported.
    progress = Signal("qint64", "qint64")
    done = Signal(str)          # empty when it worked

    def __init__(self, settings, *, spec=None, folder=None, parent=None):
        """`spec` says which extra to fetch; the video model by default."""
        super().__init__(parent)
        self.s = settings
        self.spec = spec
        self.folder = folder

    def run(self):
        """
        Fetch the extra, and never fail in silence.

        Everything is inside the try, including working out what to
        fetch. It was outside, and when a bad argument made that step
        raise, the thread died before the first progress report - so the
        bar sat at zero and nothing was ever said. A download that
        cannot start has to announce itself; an empty bar is
        indistinguishable from a slow connection.
        """
        try:
            from avcore.models import ModelInfo, download
            from avcore.setup import VIDEO_MODEL, checkpoints_dir

            spec = self.spec or VIDEO_MODEL
            folder = self.folder or checkpoints_dir(self.s)

            info = ModelInfo(
                name=spec["label"],
                description="",
                base_model="",
                nsfw=False,
                file_name=spec["name"],
                size_bytes=spec["bytes"],
                download_url=spec["url"],
            )
            download(info, folder,
                     on_progress=lambda a, b: self.progress.emit(a, b))
            self.done.emit("")
        except Exception as exc:
            self.done.emit(f"The download could not start: {exc}")


class UpdateCheck(QThread):
    """
    Asks whether a newer build exists, off the interface thread.

    On a thread for the usual reason: a slow or unreachable server would
    otherwise freeze the window, which is the fault that has bitten this
    project more than once.
    """

    answered = Signal(object)      # the newer build, or None
    failed = Signal(str)

    def run(self):
        from avcore.updates import UpdateError, check

        try:
            self.answered.emit(check())
        except UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                       # pragma: no cover
            self.failed.emit(f"The update check failed: {exc}")


class ConfirmInstall(QDialog):
    """Asks before a download measured in gigabytes."""

    def __init__(self, label, size, where, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Install model")
        self.setModal(True)
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel(f"Install {label}?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        body = QLabel(
            f"This downloads about {size} into\n{where}\n\n"
            f"ComfyUI is installed alongside it if it is not already "
            f"there. The download can be stopped and picked up later.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.addStretch(1)
        deny = QPushButton("Not now")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)
        go = QPushButton(f"Install {size}")
        go.setObjectName("confirmButton")
        go.clicked.connect(self.accept)
        row.addWidget(go)
        outer.addLayout(row)
        deny.setDefault(True)
        deny.setFocus()


class ConfirmUninstall(QDialog):
    """Asks before deleting a model, and says what it costs to undo."""

    def __init__(self, label, size, in_use, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remove model")
        self.setModal(True)
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel(f"Remove {label}?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        words = [f"This frees {size}. Getting it back means downloading "
                 f"it again."]
        if in_use:
            words.append(
                "It is the model in use, so generating will fall back to "
                "whatever else is installed, or to Pollinations if "
                "nothing is.")
        body = QLabel("\n\n".join(words))
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.addStretch(1)
        deny = QPushButton("Keep it")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)
        go = QPushButton(f"Remove and free {size}")
        go.setObjectName("confirmButton")
        go.clicked.connect(self.accept)
        row.addWidget(go)
        outer.addLayout(row)
        deny.setDefault(True)
        deny.setFocus()


class SetupPanel(QWidget):
    ready_changed = Signal(bool)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.s = engine.s
        self.worker = None
        self.thread = None
        self.launcher = ComfyLauncher(self.s)
        self._build()
        self.refresh()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        title = QLabel("Setup")
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        outer.addWidget(title)

        blurb = QLabel(
            "Images can be generated on this computer, or online without "
            "installing anything. Generating here is private, unlimited "
            "and unwatermarked, but it needs ComfyUI and a model - "
            "several gigabytes, fetched below."
        )
        blurb.setObjectName("fieldLabel")
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        outer.addWidget(self._how_section())
        outer.addWidget(self._updates_section())

        self.rows = {
            "gpu": CheckRow("Graphics card"),
            "space": CheckRow("Disk space"),
            "comfy": CheckRow("ComfyUI"),
            "model": CheckRow("Image model"),
        }
        for row in self.rows.values():
            outer.addWidget(row)

        loc = QHBoxLayout()
        loc.setSpacing(8)
        loc.addWidget(QLabel("Install to"))
        self.where = QLabel("")
        self.where.setObjectName("fieldLabel")
        self.where.setWordWrap(True)
        loc.addWidget(self.where, 1)
        self.browse = QPushButton("Change")
        self.browse.clicked.connect(self._pick_folder)
        loc.addWidget(self.browse)
        outer.addLayout(loc)

        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.setRange(0, 1000)
        self.bar.hide()
        outer.addWidget(self.bar)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.install_btn = QPushButton("Install everything")
        self.install_btn.setObjectName("primaryButton")
        self.install_btn.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.clicked.connect(self._stop)
        self.cancel_btn.hide()
        self.recheck_btn = QPushButton("Check again")
        self.recheck_btn.clicked.connect(self.refresh)

        buttons.addWidget(self.install_btn)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.recheck_btn)
        buttons.addStretch(1)
        outer.addLayout(buttons)
        outer.addStretch(1)

    # ---- state ---------------------------------------------------------

    def _how_section(self):
        """
        How images get made, and what is on disk to make them with.

        One row per option, each carrying its own buttons, because the
        two questions people actually have - "have I got this?" and "can
        I use it now?" - are about a particular model rather than about
        the list.
        """
        from avcore.setup import TIERS

        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(6)

        self.how = QButtonGroup(self)
        self.tier_rows = {}

        online = QRadioButton(
            "Online - nothing to download (Pollinations.ai)")
        online.setToolTip(
            "Images are made by Pollinations.ai. No install, no graphics "
            "card needed. They come back watermarked and it can be slow "
            "when busy.")
        self.how.addButton(online, 0)
        column.addWidget(online)
        self._tier_keys = [""]

        for index, (key, spec) in enumerate(TIERS.items(), start=1):
            row = QHBoxLayout()
            row.setSpacing(8)

            button = QRadioButton(f"On this computer - {spec['label']}")
            button.setToolTip(spec["note"])
            self.how.addButton(button, index)
            row.addWidget(button, 1)

            install = QPushButton("Install")
            install.clicked.connect(
                lambda _c=False, k=key: self._install_tier(k))
            row.addWidget(install)

            use = QPushButton("Use")
            use.setToolTip(
                "Switch to this model without going to Settings")
            use.clicked.connect(lambda _c=False, k=key: self._use_tier(k))
            row.addWidget(use)

            column.addLayout(row)

            note = QLabel("      " + spec["note"])
            note.setObjectName("fieldLabel")
            note.setWordWrap(True)
            column.addWidget(note)

            self._tier_keys.append(key)
            self.tier_rows[key] = {
                "radio": button, "install": install, "use": use,
                "note": note,
            }

        self.how.idClicked.connect(self._pick_how)

        # An extra rather than one of the choices above: it sits beside
        # whichever of them is picked, and only matters to people who
        # want it.
        from avcore.setup import VIDEO_MODEL

        extra = QHBoxLayout()
        extra.setSpacing(8)
        self.video_label = QLabel(f"      {VIDEO_MODEL['label']}")
        self.video_label.setWordWrap(True)
        extra.addWidget(self.video_label, 1)
        self.video_btn = QPushButton("Install")
        self.video_btn.clicked.connect(self._toggle_video)
        extra.addWidget(self.video_btn)
        column.addLayout(extra)

        self.video_note = QLabel("      " + VIDEO_MODEL["note"])
        self.video_note.setObjectName("fieldLabel")
        self.video_note.setWordWrap(True)
        column.addWidget(self.video_note)

        self.video_bar = QProgressBar()
        self.video_bar.setTextVisible(True)
        self.video_bar.hide()
        column.addWidget(self.video_bar)

        from avcore.setup import CUTOUT_MODEL

        cut_row = QHBoxLayout()
        cut_row.setSpacing(8)
        self.cutout_label = QLabel(f"      {CUTOUT_MODEL['label']}")
        self.cutout_label.setWordWrap(True)
        cut_row.addWidget(self.cutout_label, 1)
        self.cutout_btn = QPushButton("Install")
        self.cutout_btn.clicked.connect(self._toggle_cutout)
        cut_row.addWidget(self.cutout_btn)
        column.addLayout(cut_row)

        self.cutout_note = QLabel("      " + CUTOUT_MODEL["note"])
        self.cutout_note.setObjectName("fieldLabel")
        self.cutout_note.setWordWrap(True)
        column.addWidget(self.cutout_note)

        self.cutout_bar = QProgressBar()
        self.cutout_bar.setTextVisible(True)
        self.cutout_bar.hide()
        column.addWidget(self.cutout_bar)

        return box

    def _updates_section(self):
        """
        Whether a newer build exists, and where to get it.

        It only ever tells you and offers the page. Downloading and
        running an installer on somebody's machine is a different level
        of responsibility, and without code signing to back it up this
        app has no business taking it.
        """
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 6, 0, 0)
        row.setSpacing(10)

        self.update_note = QLabel(f"Version {__version__}")
        self.update_note.setObjectName("fieldLabel")
        self.update_note.setWordWrap(True)
        row.addWidget(self.update_note, 1)

        self.update_page_btn = QPushButton("Open download page")
        self.update_page_btn.clicked.connect(self._open_releases)
        self.update_page_btn.hide()
        row.addWidget(self.update_page_btn)

        self.update_btn = QPushButton("Check for updates")
        self.update_btn.clicked.connect(lambda: self._check_updates(True))
        row.addWidget(self.update_btn)
        return box

    def _check_updates(self, asked=False):
        """
        `asked` is True when somebody pressed the button.

        It decides how loud the result is: a background check that
        cannot reach the server says nothing, because nobody asked it a
        question. Pressing the button and getting silence would just be
        broken.
        """
        if getattr(self, "_update_worker", None) is not None \
                and self._update_worker.isRunning():
            return
        self._update_asked = asked
        if asked:
            self.update_btn.setEnabled(False)
            self.update_note.setText("Checking...")

        self._update_worker = UpdateCheck(self)
        self._update_worker.answered.connect(self._update_answer)
        self._update_worker.failed.connect(self._update_failed)
        self._update_worker.finished.connect(
            lambda: self.update_btn.setEnabled(True))
        self._update_worker.start()

    def _update_answer(self, found):
        from avcore.updates import describe

        self.update_note.setText(describe(found))
        self.update_page_btn.setVisible(bool(found))
        if found:
            self.update_page_btn.setText(
                f"Get {found['version']}")

    def _update_failed(self, message):
        if getattr(self, "_update_asked", False):
            self.update_note.setText(message)
        else:
            # Nobody asked, so nothing is said - just the version.
            self.update_note.setText(f"Version {__version__}")

    def _open_releases(self):
        import webbrowser

        from avcore.version import RELEASES_PAGE

        if RELEASES_PAGE:
            webbrowser.open(RELEASES_PAGE)

    def _sync_video(self):
        """Say whether the video extra is installed, and offer the opposite."""
        from avcore.setup import VIDEO_MODEL, video_installed

        busy = (getattr(self, "_video_worker", None) is not None
                and self._video_worker.isRunning())
        have = video_installed(self.s)

        self.video_btn.setEnabled(not busy)
        self.video_btn.setText("Uninstall" if have else "Install")
        self.video_btn.setObjectName("denyButton" if have else "")
        self.video_btn.style().unpolish(self.video_btn)
        self.video_btn.style().polish(self.video_btn)

        size = VIDEO_MODEL["bytes"] / 1_000_000_000
        self.video_note.setText(
            "      " + VIDEO_MODEL["note"]
            + ("   Installed." if have
               else f"   Not installed, {size:.1f} GB to download."))

    def _toggle_video(self):
        """Fetch the video model, or remove it."""
        # ConfirmInstall and ConfirmUninstall are defined in this
        # module, not in dialogs. Importing them from there raised, and
        # Qt swallows an exception inside a slot - so the button looked
        # dead rather than broken, which is the worst way for it to
        # fail.
        from avcore.setup import VIDEO_MODEL, video_file, video_installed

        if video_installed(self.s):
            path = video_file(self.s)
            size = f"{path.stat().st_size / 1_000_000_000:.1f} GB"
            if ConfirmUninstall(VIDEO_MODEL["label"], size, False,
                                self).exec() != QDialog.Accepted:
                return
            try:
                path.unlink()
            except OSError as exc:
                self.status.setText(
                    f"Could not remove it: {exc.strerror or exc}")
                return
            self.status.setText(
                f"Removed the video model, freeing {size}.")
            self.refresh()
            return

        size = f"{VIDEO_MODEL['bytes'] / 1_000_000_000:.1f} GB"
        if ConfirmInstall(VIDEO_MODEL["label"], size,
                          video_file(self.s).parent,
                          self).exec() != QDialog.Accepted:
            return

        self.video_bar.setRange(0, 100)
        self.video_bar.setValue(0)
        self.video_bar.show()
        # Named, not positional. `spec` was added in second place,
        # where the parent had been passed for months - so the worker
        # got the panel as the thing to download, and died on
        # spec["label"] before it ever reached the try block.
        self._video_worker = ModelFetch(self.s, parent=self)
        self._video_worker.progress.connect(self._video_progress)
        self._video_worker.done.connect(self._video_done)
        self._video_worker.start()
        self._sync_video()

    def _video_progress(self, done, total):
        if total:
            self.video_bar.setValue(int(done * 100 / total))
            self.video_bar.setFormat(_progress_text(done, total))

    def _video_done(self, message):
        self.video_bar.hide()
        if message:
            self.status.setText(message)
        else:
            self.status.setText(
                "The video model is installed. Animate this... now "
                "appears on gallery pictures.")
        self.refresh()

    def _sync_cutout(self):
        """Say whether the matting model is installed."""
        from avcore.setup import CUTOUT_MODEL, cutout_installed

        busy = (getattr(self, "_cutout_worker", None) is not None
                and self._cutout_worker.isRunning())
        have = cutout_installed(self.s)

        self.cutout_btn.setEnabled(not busy)
        self.cutout_btn.setText("Uninstall" if have else "Install")
        self.cutout_btn.setObjectName("denyButton" if have else "")
        self.cutout_btn.style().unpolish(self.cutout_btn)
        self.cutout_btn.style().polish(self.cutout_btn)

        size = CUTOUT_MODEL["bytes"] / 1_000_000_000
        self.cutout_note.setText(
            "      " + CUTOUT_MODEL["note"]
            + ("   Installed." if have
               else f"   Not installed, {size:.2f} GB to download."))

    def _toggle_cutout(self):
        """Fetch the matting model, or remove it."""
        from avcore.setup import CUTOUT_MODEL, cutout_dir, cutout_file
        from avcore.setup import cutout_installed

        if cutout_installed(self.s):
            path = cutout_file(self.s)
            size = f"{path.stat().st_size / 1_000_000:.0f} MB"
            if ConfirmUninstall(CUTOUT_MODEL["label"], size, False,
                                self).exec() != QDialog.Accepted:
                return
            try:
                path.unlink()
            except OSError as exc:
                self.status.setText(
                    f"Could not remove it: {exc.strerror or exc}")
                return
            # Pictures would otherwise keep being asked for as cut-outs
            # and quietly come back as rectangles.
            self.s.set("image.cutout", False)
            self.s.save()
            self.status.setText(
                f"Removed the cut-out model, freeing {size}.")
            self.refresh()
            return

        size = f"{CUTOUT_MODEL['bytes'] / 1_000_000:.0f} MB"
        if ConfirmInstall(CUTOUT_MODEL["label"], size,
                          cutout_dir(self.s),
                          self).exec() != QDialog.Accepted:
            return

        self.cutout_bar.setRange(0, 100)
        self.cutout_bar.setValue(0)
        self.cutout_bar.show()
        self._cutout_worker = ModelFetch(
            self.s, spec=CUTOUT_MODEL, folder=cutout_dir(self.s),
            parent=self)
        self._cutout_worker.progress.connect(self._cutout_progress)
        self._cutout_worker.done.connect(self._cutout_done)
        self._cutout_worker.start()
        self._sync_cutout()

    def _cutout_progress(self, done, total):
        if total:
            self.cutout_bar.setValue(int(done * 100 / total))
            self.cutout_bar.setFormat(_progress_text(done, total))

    def _cutout_done(self, message):
        self.cutout_bar.hide()
        self.status.setText(
            message or "Cut-out images are ready. The tick is beside the "
                       "prompt box.")
        self.refresh()

    def _sync_tiers(self):
        """
        Make the buttons say what they will do.

        Called on every refresh rather than only when something changes,
        because a model can arrive or vanish through the browser, the
        Settings panel, or Explorer.
        """
        from avcore.setup import TIERS, models_for_tier, tier_file

        current = (self.s.get("comfyui.checkpoint") or "").strip()
        online = (self.s.get("image.backend", "comfyui") or "") != "comfyui"
        here = []

        for key, row in self.tier_rows.items():
            spec = TIERS[key]
            models = models_for_tier(key, self.s)
            have = bool(models)
            if have:
                here.append(key)

            # Only what setup fetched is offered for removal. A model the
            # user found themselves also satisfies this option, but
            # deleting it from a button marked "Uninstall Stable
            # Diffusion XL" would be a nasty surprise - that belongs in
            # the model manager, where it is named.
            official = tier_file(key, self.s)
            ours = official.exists()
            row["install"].setText("Uninstall" if ours else "Install")
            row["install"].setEnabled(ours or not have)
            row["install"].setToolTip(
                "" if ours or not have else
                "Already covered by a model you installed yourself. "
                "Remove that one from Settings, Get more models.")
            row["install"].setObjectName("denyButton" if ours else "")
            # Qt only restyles on a change of name, so the widget has to
            # be told to look again.
            row["install"].style().unpolish(row["install"])
            row["install"].style().polish(row["install"])

            in_use = (not online
                      and any(current == m.name for m in models))
            row["use"].setEnabled(have and not in_use)
            row["use"].setText("In use" if in_use else "Use")

            size = f"{spec['bytes'] / 1_000_000_000:.1f} GB"
            if ours:
                detail = "   Installed."
            elif have:
                names = ", ".join(m.name for m in models[:2])
                extra = "" if len(models) <= 2 else f" and {len(models) - 2} more"
                detail = f"   Installed already: {names}{extra}"
            else:
                detail = f"   Not installed, {size} to download."
            row["note"].setText("      " + spec["note"] + detail)

        # Nothing is written here. This runs on a timer while the page
        # is visible, and a refresh that saves settings will fight the
        # person using it: the old version flipped anyone with no models
        # over to Pollinations every two seconds, including while they
        # were part way through installing one.
        if online:
            wanted = 0
        else:
            wanted = self._row_for_current(here)

        button = self.how.button(wanted)
        if button is not None and not button.isChecked():
            self.how.blockSignals(True)
            button.setChecked(True)
            self.how.blockSignals(False)

    def _row_for_current(self, here):
        """
        Which row matches the model in use.

        Matched against the models that actually satisfy each option,
        not against the official filename. Comparing filenames meant a
        checkpoint the user had found themselves matched nothing, so
        this fell back to the first installed option and dragged the tick
        back to Stable Diffusion 1.5 every couple of seconds however
        many times they clicked the other one.
        """
        from avcore.setup import models_for_tier

        current = (self.s.get("comfyui.checkpoint") or "").strip()
        if current:
            for index, key in enumerate(self._tier_keys):
                if not key:
                    continue
                if any(m.name == current for m in models_for_tier(key, self.s)):
                    return index

        # No particular model chosen: fall back to the remembered
        # choice, then to whatever is installed.
        remembered = (self.s.get("models.tier") or "").strip()
        if remembered in self._tier_keys and remembered in here:
            return self._tier_keys.index(remembered)
        if here:
            return self._tier_keys.index(here[0])
        return 0

    def _pick_how(self, index):
        """
        Record the choice straight away.

        There is no Apply on this screen, and this is the first thing
        someone does in a new install.

        Picking a model that is not installed offers to install it.
        Before, the choice was accepted, found to be unusable, and
        silently snapped back to Online - which looks like the click did
        not register rather than like a decision being asked for.
        """
        from avcore.setup import tier_installed

        key = self._tier_keys[index] if index < len(self._tier_keys) else ""
        if index == 0:
            self.s.set("image.backend", "pollinations")
            self.s.save()
            self.refresh()
            return

        if not tier_installed(key, self.s):
            # _install_tier asks first; if the answer is no, the refresh
            # at the end puts the tick back where it was.
            self._install_tier(key)
            self.refresh()
            return

        self.s.set("image.backend", "comfyui")
        self.s.set("models.tier", key)
        self._use_tier(key, quiet=True)
        self.s.save()
        self.refresh()

    def refresh(self):
        rep = avsetup.check(self.s)
        self.report = rep
        self.where.setText(str(rep["root"]))

        self.rows["gpu"].set_state(
            rep["gpu_ok"],
            rep["gpu_name"] if rep["gpu_ok"] else
            "No NVIDIA GPU detected. Image generation needs one.")

        self.rows["space"].set_state(
            rep["space_ok"],
            f"{avsetup.human(rep['free_bytes'])} free"
            + ("" if rep["space_ok"] else
               f", and about {avsetup.human(avsetup.REQUIRED_FREE_BYTES)} "
               f"is needed"))

        self.rows["comfy"].set_state(
            rep["comfy_ok"],
            "Installed" if rep["comfy_ok"] else "Not installed yet")

        if rep["model_ok"]:
            names = ", ".join(p.name for p in rep["models"][:3])
            self.rows["model"].set_state(True, names)
        else:
            self.rows["model"].set_state(False, "No model yet")

        if rep["ready"]:
            self.status.setText(
                "Everything is in place. You can start listening.")
            self.install_btn.setText("Reinstall")
            self.install_btn.setObjectName("")
        else:
            self.install_btn.setText("Install everything")
            self.install_btn.setObjectName("primaryButton")
            if not rep["gpu_ok"]:
                self.status.setText(
                    "Without an NVIDIA GPU the rest will install but images "
                    "will not generate.")
            else:
                self.status.setText("")

        # Re-apply the stylesheet so the object name change takes effect.
        self.install_btn.style().unpolish(self.install_btn)
        self.install_btn.style().polish(self.install_btn)
        # The per-model rows read the same report, so they are brought
        # up to date in the same pass rather than on their own timer.
        self._sync_tiers()
        self._sync_video()
        self._sync_cutout()
        self.ready_changed.emit(rep["ready"])
        return rep

    def _pick_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Where should ComfyUI and the model go?",
            str(self.report["root"]))
        if chosen:
            self.s.set("comfyui.path", chosen)
            self.s.save()
            self.refresh()

    # ---- one model at a time ---------------------------------------------

    def _use_tier(self, key, quiet=False):
        """
        Switch generation to this model.

        A shortcut for what Settings does, put here because someone
        standing in front of the list of models should not have to go
        and find another screen to pick one.
        """
        from avcore.setup import TIERS, models_for_tier

        spec = TIERS.get(key)
        if spec is None:
            return
        models = models_for_tier(key, self.s)
        if not models:
            if not quiet:
                self.status.setText(
                    f"{spec['label']} is not installed yet.")
            return

        # Whichever model actually satisfies this option - the official
        # one if setup fetched it, otherwise the user's own.
        self.s.set("image.backend", "comfyui")
        self.s.set("comfyui.checkpoint", models[0].name)
        self.s.set("models.tier", key)
        self.s.save()
        if not quiet:
            self.status.setText(f"Now generating with {spec['label']}.")
        self.refresh()

    def _install_tier(self, key):
        """Install or remove one model, depending on what is there."""
        from avcore.setup import TIERS, tier_file, tier_installed

        spec = TIERS.get(key)
        if spec is None:
            return

        if tier_file(key, self.s).exists():
            self._uninstall_tier(key)
            return

        size = f"{spec['bytes'] / 1_000_000_000:.1f} GB"
        where = tier_file(key, self.s).parent
        if ConfirmInstall(spec["label"], size, where,
                          self).exec() != QDialog.Accepted:
            return

        # ComfyUI comes with it if it is not there yet, which is what the
        # existing installer already does - only the model it fetches
        # depends on the choice.
        self.s.set("models.tier", key)
        self.s.save()
        self._start()

    def _uninstall_tier(self, key):
        from avcore.setup import TIERS, tier_file

        spec = TIERS[key]
        path = tier_file(key, self.s)
        size = f"{path.stat().st_size / 1_000_000_000:.1f} GB"
        in_use = (self.s.get("comfyui.checkpoint") or "") == spec["name"]

        if ConfirmUninstall(spec["label"], size, in_use,
                            self).exec() != QDialog.Accepted:
            return

        try:
            path.unlink()
        except OSError as exc:
            # Almost always ComfyUI holding the file open.
            self.status.setText(
                f"Could not remove {spec['label']}: "
                f"{exc.strerror or exc}. If ComfyUI is running with this "
                f"model loaded, stop it and try again.")
            return

        if in_use:
            self.s.set("comfyui.checkpoint", "")
        self.s.save()
        self.status.setText(f"Removed {spec['label']}, freeing {size}.")
        self.refresh()

    # ---- installing ----------------------------------------------------

    def _start(self):
        if self.thread and self.thread.is_alive():
            return
        root = self.report["root"]

        self.worker = SetupWorker(self.s, root)
        self.worker.progress.connect(self._on_progress)
        self.worker.step.connect(self._on_step)
        self.worker.finished.connect(self._on_finished)

        self.install_btn.hide()
        self.recheck_btn.hide()
        self.browse.setEnabled(False)
        self.cancel_btn.show()
        self.bar.show()
        self.bar.setValue(0)
        self.status.setText("Starting...")

        self.thread = threading.Thread(
            target=self.worker.run, name="av-setup", daemon=True)
        self.thread.start()

    def _stop(self):
        if self.worker:
            self.worker.cancel()
        self.status.setText("Stopping...")

    def _on_step(self, _key, text):
        self.status.setText(text)

    def _on_progress(self, info):
        label = info.get("label", "")
        done = info.get("done", 0)
        total = info.get("total", 0) or 0
        note = info.get("note", "")

        if total:
            self.bar.setValue(int(1000 * done / total))
            pct = 100 * done / total
        else:
            pct = 0

        if note == "already here":
            self.status.setText(f"{label}: already downloaded")
            return

        speed = info.get("speed") or 0
        bits = [f"{label}: {pct:.0f}%"]
        if label == "Unpacking":
            bits = [f"Unpacking: {pct:.0f}%"]
        else:
            bits.append(
                f"{avsetup.human(done)} of {avsetup.human(total)}"
                if total else avsetup.human(done))
            if speed > 0:
                bits.append(f"{avsetup.human(speed)}/s")
                if total and done < total:
                    left = (total - done) / speed
                    bits.append(f"{int(left // 60)}m {int(left % 60)}s left")
        self.status.setText("   ".join(bits))

    def _on_finished(self, ok, message):
        self.bar.hide()
        self.cancel_btn.hide()
        self.install_btn.show()
        self.recheck_btn.show()
        self.browse.setEnabled(True)
        self.status.setText(message)
        self.refresh()

    def showEvent(self, event):
        """
        Re-check on arrival, and keep checking while visible.

        A model can appear or vanish from the browser, from Settings, or
        from Explorer, and this page is the one claiming to say what is
        installed. Polling only while it is on screen keeps it honest
        without costing anything the rest of the time - the check reads
        cached headers and takes about a hundredth of a second.
        """
        super().showEvent(event)
        self._default_to_online_once()
        self.refresh()
        if not getattr(self, "_update_checked", False):
            # Once per run, quietly. Nobody wants to be told about an
            # update every time they glance at this page.
            self._update_checked = True
            self._check_updates(False)
        if not hasattr(self, "_watch"):
            self._watch = QTimer(self)
            self._watch.setInterval(2000)
            self._watch.timeout.connect(self._recheck_models)
        self._watch.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        watch = getattr(self, "_watch", None)
        if watch is not None:
            watch.stop()

    def _default_to_online_once(self):
        """
        Start a fresh install on the online option.

        Only when nothing is installed and nothing has ever been chosen,
        and only once - this is a helpful starting point, not a rule to
        enforce against someone who has picked ComfyUI and is about to
        install a model.
        """
        from avcore.setup import installed_tiers

        if getattr(self, "_defaulted", False):
            return
        self._defaulted = True

        chosen_before = bool((self.s.get("models.tier") or "").strip()
                             or (self.s.get("comfyui.checkpoint") or "").strip())
        if chosen_before or installed_tiers(self.s):
            return
        if (self.s.get("image.backend", "comfyui") or "") == "comfyui":
            self.s.set("image.backend", "pollinations")
            self.s.save()

    def _recheck_models(self):
        """
        Refresh only when what is installed has actually changed.

        A full refresh every two seconds would fight with anything the
        user is doing, so the cheap question is asked first.
        """
        from avcore.setup import installed_tiers

        if self.thread and self.thread.is_alive():
            return
        here = tuple(installed_tiers(self.s))
        if here != getattr(self, "_last_seen_models", None):
            self._last_seen_models = here
            self.refresh()

    def on_state(self, _snapshot):
        # Setup reflects the filesystem, not the engine.
        pass
