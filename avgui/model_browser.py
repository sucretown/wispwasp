"""
Browsing and fetching image models.

Everything that touches the network runs on a thread. That is not
caution for its own sake: a blocking call on the interface thread is
exactly the fault that froze the whole window when ComfyUI stopped
answering, and a catalogue search can easily take several seconds.
"""

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from avcore.models import (
    BASE_MODELS, CatalogueError, NeedsAccount, download, installed,
    part_files, search,
)


def _size(byte_count):
    """
    A size a person can judge.

    Gigabytes for models, megabytes for anything smaller - a list of
    part files all reading "0.00 GB" tells you nothing about which one
    is worth clearing.
    """
    # Decimal, like the page the file came from. See human() in
    # avcore.setup for why this matters more than the rounding suggests.
    if byte_count >= 1_000_000_000:
        return f"{byte_count / 1_000_000_000:.2f} GB"
    if byte_count >= 1_000_000:
        return f"{byte_count / 1_000_000:.0f} MB"
    return f"{byte_count / 1000:.0f} KB"


class SearchWorker(QThread):
    """Asks the catalogue, off the interface thread."""

    found = Signal(list)
    failed = Signal(str)

    def __init__(self, query, base_model, include_adult, api_key,
                 kind="Checkpoint",
                 parent=None):
        super().__init__(parent)
        self.query = query
        self.base_model = base_model
        self.include_adult = include_adult
        self.api_key = api_key
        self.kind = kind

    def run(self):
        try:
            self.found.emit(search(
            kind=self.kind,
                query=self.query, base_model=self.base_model,
                include_adult=self.include_adult, limit=30,
                api_key=self.api_key))
        except (CatalogueError, NeedsAccount) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                       # pragma: no cover
            self.failed.emit(f"Something went wrong: {exc}")


class DownloadWorker(QThread):
    """
    Fetches one model, reporting progress and able to stop.

    Stopping is not cancelling: what has arrived stays on disk as a part
    file, and asking for the same model again carries on from there.
    """

    # 64-bit, not Signal(int, int). Qt's int is 32 bits, so any file over
    # 2.1GB wrapped: a 4.27GB download arrived as -29,870,600 and the bar
    # read "0.14 of -0.03 GB". The larger models were worse, wrapping to
    # plausible-looking numbers rather than obviously wrong ones - 9.6GB
    # came through as 5.3GB, which nobody would question.
    progress = Signal("qint64", "qint64")
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, info, folder, api_key, parent=None):
        super().__init__(parent)
        self.info = info
        self.folder = folder
        self.api_key = api_key
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            path = download(
                self.info, self.folder,
                on_progress=lambda a, b: self.progress.emit(a, b),
                should_stop=lambda: self._stop,
                api_key=self.api_key)
            self.done.emit(path)
        except (CatalogueError, NeedsAccount) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                       # pragma: no cover
            self.failed.emit(f"The download failed: {exc}")


class ModelRow(QWidget):
    """One catalogue entry, laid out so the size is impossible to miss."""

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        row = QVBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(8)
        name = QLabel(info.name)
        name.setStyleSheet("font-weight: 600;")
        head.addWidget(name)
        head.addStretch(1)
        # The number that decides whether someone wants this at all.
        size = QLabel(info.size_label)
        size.setStyleSheet("font-weight: 600;")
        head.addWidget(size)
        row.addLayout(head)

        bits = [info.base_model]
        if info.creator:
            bits.append(f"by {info.creator}")
        if info.nsfw:
            bits.append("adult")
        detail = QLabel("   ".join(b for b in bits if b))
        detail.setObjectName("fieldLabel")
        row.addWidget(detail)

        if info.description:
            blurb = QLabel(info.description[:160]
                           + ("..." if len(info.description) > 160 else ""))
            blurb.setObjectName("fieldLabel")
            blurb.setWordWrap(True)
            row.addWidget(blurb)


class ConfirmDownload(QDialog):
    """
    Asks before starting a download measured in gigabytes.

    States the size and where it is going, because those are the two
    things someone might object to after the fact.
    """

    def __init__(self, info, folder, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download model")
        self.setModal(True)
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel(f"Download {info.name}?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        body = QLabel(
            f"This is {info.size_label} and will be saved into\n"
            f"{folder}\n\n"
            f"{info.licence}. It can be stopped part way and picked up "
            f"again later.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.addStretch(1)
        deny = QPushButton("Not now")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)
        go = QPushButton(f"Download {info.size_label}")
        go.setObjectName("confirmButton")
        go.clicked.connect(self.accept)
        row.addWidget(go)
        outer.addLayout(row)

        # Nothing several gigabytes long starts on a stray Enter.
        deny.setDefault(True)
        deny.setFocus()


class ConfirmRemove(QDialog):
    """
    Asks before deleting a model file.

    Says how big it is, because reclaiming the space is usually the
    whole point, and warns if it is the one currently selected - that
    would otherwise be discovered at the next generation.
    """

    def __init__(self, path, size_label, in_use, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remove model")
        self.setModal(True)
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel(f"Delete {path.name}?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        words = [f"This frees {size_label}. The file is deleted from "
                 f"disk and cannot be recovered - it would have to be "
                 f"downloaded again."]
        if in_use:
            words.append(
                "This is the model currently selected, so generation "
                "will fall back to whichever is found first.")
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
        go = QPushButton(f"Delete and free {size_label}")
        go.setObjectName("confirmButton")
        go.clicked.connect(self.accept)
        row.addWidget(go)
        outer.addLayout(row)

        deny.setDefault(True)
        deny.setFocus()


class ModelBrowser(QDialog):
    """The 'Get more models' window."""

    installed_changed = Signal()

    def __init__(self, settings, *, kind="Checkpoint", parent=None):
        """
        `kind` is keyword-only on purpose.

        It was added in second place, where callers had been passing a
        parent widget positionally for months. Making it keyword-only
        turns that mistake into an immediate TypeError rather than a
        widget quietly being treated as a string.
        """
        super().__init__(parent)
        self.s = settings
        self.kind = kind
        self.is_lora = kind.upper() == "LORA"
        # The word used throughout the dialog, so nothing says "model"
        # at somebody looking for a LoRA.
        self.noun = "LoRA" if self.is_lora else "model"
        self.setWindowTitle("LoRAs" if self.is_lora else "Models")
        self.setModal(True)
        self.resize(660, 580)

        self._search = None
        self._download = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Models")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        # Two tabs rather than two windows: finding a model and deciding
        # you have too many are the same errand, and the disk figure on
        # one side is what makes the other side make sense.
        self.tabs = QTabWidget()
        self.tabs.addTab(self._find_page(),
                         "Find LoRAs" if self.is_lora
                         else "Find models")
        self.tabs.addTab(self._installed_page(), "Installed")
        self.tabs.currentChanged.connect(self._tab_changed)
        outer.addWidget(self.tabs, 1)

        self.note = QLabel("")
        self.note.setObjectName("fieldLabel")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)

        self.bar = QProgressBar()
        self.bar.setTextVisible(True)
        self.bar.hide()
        outer.addWidget(self.bar)

        feet = QHBoxLayout()
        feet.addStretch(1)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("denyButton")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.hide()
        feet.addWidget(self.stop_btn)
        self.get = QPushButton("Download")
        self.get.setObjectName("primaryButton")
        self.get.setEnabled(False)
        self.get.clicked.connect(self._start)
        feet.addWidget(self.get)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        feet.addWidget(close)
        outer.addLayout(feet)

        self.look()

    def _find_page(self):
        """The catalogue side."""
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 10, 0, 0)
        column.setSpacing(8)

        blurb = QLabel(
            f"Downloaded from Civitai into ComfyUI's "
            f"{'loras' if self.is_lora else 'checkpoints'} folder, "
            f"and listed once they arrive.")
        blurb.setObjectName("fieldLabel")
        blurb.setWordWrap(True)
        column.addWidget(blurb)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search by name, or leave empty")
        self.query.returnPressed.connect(self.look)
        controls.addWidget(self.query, 1)

        self.base = QComboBox()
        for key in BASE_MODELS:
            self.base.addItem(key, key)
        self.base.currentIndexChanged.connect(lambda _i: self.look())
        controls.addWidget(self.base)

        # An ordinary button: searching changes nothing, and the primary
        # colour is reserved for actions that do. It follows whichever
        # theme is selected, like every other plain button.
        self.go = QPushButton("Search")
        self.go.clicked.connect(self.look)
        controls.addWidget(self.go)
        column.addLayout(controls)

        self.adult = QCheckBox("Show models flagged as adult")
        # Safe mode owns this tick while it is on. Disabled rather than
        # hidden, with the reason on it: a control that silently stops
        # working is worse than one that says why it cannot.
        from avcore.safety import is_on as _safe_mode

        if _safe_mode(self.s):
            self.adult.setChecked(False)
            self.adult.setEnabled(False)
            self.adult.setToolTip(
                "Safe mode is on, in Settings. Turn it off to browse "
                "these.")
        self.adult.setToolTip(
            "Changes which models are listed. It does not restrain what "
            "any model can produce - that is decided by the model "
            "itself, whatever the catalogue says about it.")
        self.adult.toggled.connect(lambda _v: self.look())
        column.addWidget(self.adult)

        self.list = QListWidget()
        self.list.setObjectName("feedList")
        self.list.itemSelectionChanged.connect(self._sync)
        column.addWidget(self.list, 1)

        # Its own status line. A single shared one meant a search
        # finishing in the background wiped out the message about what
        # had just been deleted on the other tab.
        self.find_note = QLabel("")
        self.find_note.setObjectName("fieldLabel")
        self.find_note.setWordWrap(True)
        column.addWidget(self.find_note)
        return page

    def _installed_page(self):
        """What is already on disk, and what it costs."""
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 10, 0, 0)
        column.setSpacing(8)

        self.disk = QLabel("")
        self.disk.setStyleSheet("font-weight: 600;")
        column.addWidget(self.disk)

        self.have = QListWidget()
        self.have.setObjectName("feedList")
        self.have.itemSelectionChanged.connect(self._sync_installed)
        column.addWidget(self.have, 1)

        row = QHBoxLayout()
        self.leftovers = QLabel("")
        self.leftovers.setObjectName("fieldLabel")
        row.addWidget(self.leftovers, 1)
        self.tidy = QPushButton("Clear unfinished")
        self.tidy.setToolTip(
            "Deletes part-finished downloads. Anything cleared has to "
            "start from the beginning next time.")
        self.tidy.clicked.connect(self._tidy)
        self.tidy.hide()
        row.addWidget(self.tidy)
        self.remove = QPushButton("Remove")
        self.remove.setObjectName("denyButton")
        self.remove.setEnabled(False)
        self.remove.clicked.connect(self._remove)
        row.addWidget(self.remove)
        column.addLayout(row)

        self.manage_note = QLabel("")
        self.manage_note.setObjectName("fieldLabel")
        self.manage_note.setWordWrap(True)
        column.addWidget(self.manage_note)

        # Anyone who downloaded a model on a portable install before the
        # folder was worked out properly has gigabytes sitting where
        # ComfyUI never looks. Rather than leave them to find that out,
        # the row appears only when there is something to rescue.
        stray_row = QHBoxLayout()
        self.stray_note = QLabel("")
        self.stray_note.setObjectName("fieldLabel")
        self.stray_note.setWordWrap(True)
        stray_row.addWidget(self.stray_note, 1)
        self.stray_btn = QPushButton("Move them")
        self.stray_btn.clicked.connect(self._adopt_strays)
        stray_row.addWidget(self.stray_btn)
        self.stray_note.hide()
        self.stray_btn.hide()
        column.addLayout(stray_row)
        return page

    # ---- where things go -------------------------------------------------

    def target_folder(self):
        """Where a download of this kind belongs."""
        from avcore.setup import checkpoints_dir, loras_dir

        if self.is_lora:
            return loras_dir(self.s)
        return checkpoints_dir(self.s)

    def checkpoints_folder(self):
        """
        ComfyUI's checkpoints folder.

        Asked of the locator rather than assumed. There are two layouts:
        a git clone keeps models under <root>/models, while the portable
        build - which is what setup installs - buries them under
        <root>/ComfyUI_windows_portable/ComfyUI/models. Assuming the
        first meant that on a portable install every download landed in
        a folder ComfyUI never reads, so models arrived and then simply
        were not there.
        """
        from avcore.setup import checkpoints_dir

        return checkpoints_dir(self.s)

    def _key(self):
        return (self.s.get("models.civitai_key") or "").strip() or None

    # ---- what is installed ----------------------------------------------

    def _tab_changed(self, index):
        """
        The installed list is re-read on arrival rather than kept live.

        A download may have finished, or a file been deleted from
        outside the app, while this window sat open.
        """
        on_installed = self.tabs.tabText(index) == "Installed"
        if on_installed:
            self.refresh_installed()
        # Download belongs to the other tab. Leaving it sitting there
        # greyed out just invites the question of why it cannot be
        # pressed.
        self.get.setVisible(not on_installed)
        self._sync()
        self._sync_installed()

    def refresh_installed(self):
        folder = self.target_folder()
        self.have.clear()

        files = installed(folder)
        total = 0
        current = (self.s.get("comfyui.checkpoint") or "").strip()
        for path in files:
            size = path.stat().st_size
            total += size
            label = f"{path.name}\n{_size(size)}"
            if path.name == current:
                # Worth saying: removing this one changes what gets
                # generated, not just what is on disk.
                label += "    in use"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, path)
            self.have.addItem(item)

        if files:
            self.disk.setText(
                f"{len(files)} models, {_size(total)} in {folder}")
        else:
            self.disk.setText(
                f"No {self.noun}s yet in {folder}")

        self._show_strays()

        # Part files are the app's own litter, so it offers to sweep it.
        leftovers = part_files(folder)
        if leftovers:
            spare = sum(p.stat().st_size for p in leftovers)
            self.leftovers.setText(
                f"{len(leftovers)} unfinished download"
                f"{'s' if len(leftovers) != 1 else ''}, {_size(spare)}")
            self.tidy.show()
        else:
            self.leftovers.setText("")
            self.tidy.hide()

    def _show_strays(self):
        """Say so when models are somewhere ComfyUI cannot see them."""
        from avcore.setup import stray_checkpoints

        strays = stray_checkpoints(self.s)
        if not strays:
            self.stray_note.hide()
            self.stray_btn.hide()
            return

        total = sum(path.stat().st_size for path in strays)
        many = len(strays) != 1
        self.stray_note.setText(
            f"{len(strays)} model{'s' if many else ''} ({_size(total)}) "
            f"{'are' if many else 'is'} in a folder ComfyUI does not "
            f"read, left there by an older version of this app. "
            f"{'They' if many else 'It'} can be moved into place.")
        self.stray_note.show()
        self.stray_btn.show()

    def _adopt_strays(self):
        from avcore.setup import adopt_strays

        moved, skipped, failed = adopt_strays(self.s)
        parts = []
        if moved:
            parts.append(f"Moved {moved} into place")
        if skipped:
            parts.append(f"{skipped} already there")
        if failed:
            parts.append(f"{failed} could not be moved")
        self.manage_note.setText(
            ". ".join(parts) + "." if parts else "Nothing to move.")
        self.refresh_installed()
        self.installed_changed.emit()

    def _sync_installed(self):
        busy = self._download is not None and self._download.isRunning()
        self.remove.setEnabled(
            self.have.currentItem() is not None and not busy)

    def _remove(self):
        chosen = self.have.currentItem()
        if chosen is None:
            return
        path = chosen.data(Qt.UserRole)
        size = path.stat().st_size
        label = _size(size)
        in_use = path.name == (self.s.get("comfyui.checkpoint") or "").strip()

        if ConfirmRemove(path, label, in_use, self).exec() != QDialog.Accepted:
            return

        try:
            path.unlink()
        except OSError as exc:
            # Usually ComfyUI still has it open. Saying so beats a raw
            # WinError, which reads like a fault in this app.
            self.manage_note.setText(
                f"Could not delete {path.name}: {exc.strerror or exc}. "
                f"If ComfyUI has it loaded, stop ComfyUI and try again.")
            return

        if in_use:
            # Pointing at a file that is gone would fail at the next
            # generation, so it falls back to whatever is found first.
            self.s.set("comfyui.checkpoint", "")
            self.s.save()

        self.manage_note.setText(f"Removed {path.name}, freeing {label}.")
        self.refresh_installed()
        self.installed_changed.emit()

    def _tidy(self):
        folder = self.target_folder()
        freed = 0
        failed = 0
        for path in part_files(folder):
            try:
                freed += path.stat().st_size
                path.unlink()
            except OSError:
                failed += 1
        self.manage_note.setText(
            f"Cleared {_size(freed)} of unfinished downloads."
            + (f" {failed} could not be deleted." if failed else ""))
        self.refresh_installed()

    # ---- searching -------------------------------------------------------

    def look(self):
        if self._search is not None and self._search.isRunning():
            return
        self.list.clear()
        self.get.setEnabled(False)
        self.find_note.setText("Looking...")
        self.go.setEnabled(False)

        self._search = SearchWorker(
            self.query.text(), self.base.currentData(),
            self.adult.isChecked(), self._key(), self.kind, self)
        self._search.found.connect(self._show)
        self._search.failed.connect(self._trouble)
        self._search.finished.connect(lambda: self.go.setEnabled(True))
        self._search.start()

    def _show(self, models):
        from avcore.safety import is_on as _safe_mode

        if _safe_mode(self.s):
            # Belt as well as braces: the request already asks for
            # none, but a catalogue can mislabel its own entries and
            # this costs nothing.
            models = [m for m in models if not m.nsfw]

        self.list.clear()
        for info in models:
            item = QListWidgetItem()
            row = ModelRow(info)
            item.setSizeHint(row.sizeHint())
            item.setData(Qt.UserRole, info)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        if models:
            self.find_note.setText(
                f"{len(models)} models. Pick one to download.")
        else:
            self.find_note.setText(
                "Nothing matched. Try a different search, or the other "
                "base model.")

    def _trouble(self, message):
        self.list.clear()
        self.find_note.setText(message)

    def _sync(self):
        chosen = self.list.currentItem()
        self.get.setEnabled(
            chosen is not None
            and (self._download is None or not self._download.isRunning()))

    # ---- downloading -----------------------------------------------------

    def _start(self):
        chosen = self.list.currentItem()
        if chosen is None:
            return
        info = chosen.data(Qt.UserRole)
        folder = self.target_folder()

        if ConfirmDownload(info, folder, self).exec() != QDialog.Accepted:
            return

        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFormat(f"{info.name}  %p%")
        self.bar.show()
        self.stop_btn.show()
        self.get.setEnabled(False)
        self.note.setText(f"Downloading {info.name}...")

        self._download = DownloadWorker(info, folder, self._key(), self)
        self._download.progress.connect(self._progress)
        self._download.done.connect(lambda path: self._arrived(info, path))
        self._download.failed.connect(self._download_failed)
        self._download.start()

    def _progress(self, done, total):
        if total:
            self.bar.setValue(int(done * 100 / total))
            # Bytes mean nothing at this scale; gigabytes do.
            self.bar.setFormat(
                f"%p%   {done / 1_000_000_000:.2f} of "
                f"{total / 1_000_000_000:.2f} GB")

    def _arrived(self, info, path):
        self.bar.hide()
        self.stop_btn.hide()
        self._sync()
        if path is None:
            self.note.setText(
                f"Stopped. What arrived is kept, so downloading "
                f"{info.name} again will carry on from there.")
            return
        self.note.setText(
            f"{info.name} is installed. It will appear in the model "
            f"list - ComfyUI may need a restart to notice it.")
        self.installed_changed.emit()

    def _download_failed(self, message):
        self.bar.hide()
        self.stop_btn.hide()
        self._sync()
        self.note.setText(message)

    def _stop(self):
        if self._download is not None and self._download.isRunning():
            self._download.stop()
            self.note.setText("Stopping...")

    def closeEvent(self, event):
        """
        Leave nothing running behind the window.

        A thread still writing to disk after its window has gone is how
        a half-written file ends up looking finished.
        """
        for worker in (self._download, self._search):
            if worker is not None and worker.isRunning():
                if hasattr(worker, "stop"):
                    worker.stop()
                worker.wait(5000)
        super().closeEvent(event)
