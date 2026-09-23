"""
Settings panel.

Changes write straight to settings.json. Anything the running engine reads
per-cycle takes effect on the next cycle; the few things that don't are
labelled as needing a restart rather than silently doing nothing.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from .dialogs import ConfirmPrune

LAYOUTS = [
    ("hybrid", "Hybrid - sidebar with a prompt bar always visible"),
    ("sidebar", "Panels - one view at a time"),
    ("split", "Split - live view and prompt side by side"),
]

SPEECH_MODELS = ["tiny.en", "base.en", "small.en", "medium.en"]


def index_of(combo, value):
    """
    Find a combo entry by its data, comparing by value.

    QComboBox.findData wraps its argument in a QVariant, and for an
    arbitrary Python object that comparison is by identity - so a tuple
    rebuilt from the settings never matches the equal tuple stored in the
    combo, and the lookup silently returns -1. Strings happen to work,
    which is why only the capture picker showed the problem: it is the one
    control whose data is a tuple.
    """
    for i in range(combo.count()):
        if combo.itemData(i) == value:
            return i
    return -1


def heading(text):
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size: 14px; font-weight: 600; padding-top: 6px;")
    return lbl


def hint(text):
    lbl = QLabel(text)
    lbl.setObjectName("fieldLabel")
    lbl.setWordWrap(True)
    return lbl


def _set_item_visible(item, visible):
    """
    Show or hide whatever a layout item is holding.

    A form block is a layout rather than a widget, so it has no
    visibility of its own and its children have to be walked.
    """
    if item is None:
        return
    widget = item.widget()
    if widget is not None:
        widget.setVisible(visible)
        return
    layout = item.layout()
    if layout is not None:
        for index in range(layout.count()):
            _set_item_visible(layout.itemAt(index), visible)


class SettingsPanel(QWidget):
    """
    Every setting, and the machinery for holding changes back.

    In confirm mode nothing is written until Apply is pressed: edits are
    held as pending values and the originals kept alongside, so Cancel can
    put every control back and a crash loses nothing that was never
    applied. Turning the option off writes each change as it is made,
    which is how this panel behaved before.
    """

    dirty_changed = Signal(bool)

    def __init__(self, engine, on_layout_change=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.s = engine.s
        self.on_layout_change = on_layout_change
        self._holders = []
        self._loading = True
        # key -> (original value, pending value)
        self._pending = {}
        # key -> callable putting a widget back to a given value
        self._restorers = {}
        # key -> the control that edits it
        self._widgets = {}
        self._build()
        self._loading = False

    # ---- pending changes ------------------------------------------------

    def confirm_mode(self):
        return bool(self.s.get("ui.confirm_settings", True))

    def is_dirty(self):
        return bool(self._pending)

    def _note_pending(self, key, value):
        """
        Record an edit, or forget it if the value came back on its own.

        Returning a control to what it was is the same as never having
        touched it, so the bar goes away rather than asking about a change
        that no longer exists.
        """
        original = self.s.get(key)
        if key in self._pending:
            original = self._pending[key][0]
        if value == original:
            self._pending.pop(key, None)
        else:
            self._pending[key] = (original, value)
        self.dirty_changed.emit(self.is_dirty())

    def apply_pending(self):
        """Write every held change, then run whatever it affects."""
        if not self._pending:
            return False
        if not self._confirm_prune():
            # The limit was withdrawn; the rest of the changes still apply.
            if not self._pending:
                return False
        changed = dict(self._pending)
        for key, (_old, new) in changed.items():
            self.s.set(key, new)
        self.s.save()
        self._pending.clear()
        self.dirty_changed.emit(False)
        self._run_side_effects(changed.keys())
        return True

    def _confirm_prune(self):
        """
        Check before a lower "keep last" throws images away.

        Asked here rather than when the number is typed, because in
        confirm mode nothing is real until Apply - warning about a
        deletion that might never happen would be worse than not warning
        at all.
        """
        if "image.keep_images" not in self._pending:
            return True
        old, new = self._pending["image.keep_images"]
        if new >= old:
            return True

        counter = getattr(self.engine, "prunable_count", None)
        if counter is None:
            return True
        doomed = counter(int(new))
        if doomed <= 0:
            return True

        if ConfirmPrune(doomed, int(new), self).exec() == QDialog.Accepted:
            return True

        # Put that one control back and leave the rest of the changes.
        restore = self._restorers.get("image.keep_images")
        was, self._loading = self._loading, True
        try:
            if restore:
                restore(old)
        finally:
            self._loading = was
        self._pending.pop("image.keep_images", None)
        self.dirty_changed.emit(self.is_dirty())
        return False

    def cancel_pending(self):
        """Put every control back to the value it had."""
        if not self._pending:
            return False
        restoring, self._loading = self._loading, True
        try:
            for key, (old, _new) in self._pending.items():
                restore = self._restorers.get(key)
                if restore:
                    restore(old)
        finally:
            self._loading = restoring
        self._pending.clear()
        self.dirty_changed.emit(False)
        # The mode controls grey themselves from the live value, so they
        # need re-syncing after a restore.
        self._apply_mode()
        self._check_suffix()
        return True

    def _run_side_effects(self, keys):
        """Things that have to happen when particular settings land."""
        keys = set(keys)
        # The tick is held by the confirm bar like any other change, so
        # the note cannot follow the tick itself - it has to follow the
        # setting landing. Without this, ticking and pressing Apply
        # changed the behaviour and showed nothing at all.
        self._sync_avoid_note()
        if any(k.startswith("audio.") for k in keys):
            reload_audio = getattr(self.engine, "reload_audio", None)
            if reload_audio:
                reload_audio()
        if "ui.layout" in keys and self.on_layout_change:
            self.on_layout_change(self.s.get("ui.layout"))
        if "image.keep_images" in keys:
            # Apply the new limit now rather than waiting for the next
            # image to trigger a prune.
            prune = getattr(self.engine, "prune_now", None)
            if prune:
                prune()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        # Form fields stretch to the row width by default, which turns a
        # three-digit number box into a 550px trough. Capping the column
        # keeps labels and fields within a readable measure.
        inner.setMaximumWidth(680)
        self.form = QVBoxLayout(inner)
        self.form.setContentsMargins(18, 16, 18, 24)
        self.form.setSpacing(8)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        self._audio_section()
        self._speech_section()
        self._image_section()
        self._comfy_section()
        self._paths_section()
        self._ui_section()
        self.form.addStretch(1)

    # ---- helpers -------------------------------------------------------

    def _rows(self):
        """A form block that shares the panel's left margin."""
        box = QFormLayout()
        box.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        box.setHorizontalSpacing(14)
        box.setVerticalSpacing(8)
        box.setContentsMargins(0, 4, 0, 10)
        self.form.addLayout(box)
        return box

    def _make_spin(self, key, lo, hi, suffix="", step=1, width=130):
        """A bound spin box on its own, for callers arranging their own row."""
        w = QSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        if suffix:
            w.setSuffix(suffix)
        w.setValue(int(self.s.get(key, lo)))
        w.setFixedWidth(width)
        w.valueChanged.connect(lambda v, k=key: self._save(k, v))
        self._restorers[key] = lambda v, wid=w: wid.setValue(int(v))
        self._widgets[key] = w
        return w

    def _bind_spin(self, key, lo, hi, suffix="", step=1):
        return self._pin_left(self._make_spin(key, lo, hi, suffix, step))

    def _bind_double(self, key, lo, hi, step=0.5):
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setDecimals(1)
        w.setValue(float(self.s.get(key, lo)))
        w.setFixedWidth(130)
        w.valueChanged.connect(lambda v, k=key: self._save(k, v))
        self._restorers[key] = lambda v, wid=w: wid.setValue(float(v))
        self._widgets[key] = w
        return self._pin_left(w)

    def _pin_left(self, widget):
        """
        Stop a fixed-width control being stretched across the form row.

        The holder is kept in a list because Qt's C++ side owns its
        children: if the holder were only a local, Python would collect it
        and take the live widget down with it.
        """
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(widget)
        row.addStretch(1)
        self._holders.append(holder)
        return holder

    def _bind_text(self, key, placeholder=""):
        w = QLineEdit(str(self.s.get(key, "") or ""))
        if placeholder:
            w.setPlaceholderText(placeholder)
        w.editingFinished.connect(
            lambda k=key, wid=w: self._save(k, wid.text().strip()))
        self._restorers[key] = lambda v, wid=w: wid.setText(str(v or ""))
        self._widgets[key] = w
        return w

    def _bind_check(self, key, label):
        w = QCheckBox(label)
        w.setChecked(bool(self.s.get(key, False)))
        w.toggled.connect(lambda v, k=key: self._save(k, v))
        self._restorers[key] = lambda v, wid=w: wid.setChecked(bool(v))
        self._widgets[key] = w
        return w

    def _save(self, key, value):
        """
        Take an edit: held back in confirm mode, written immediately if not.
        """
        if self._loading:
            return
        if self.confirm_mode():
            self._note_pending(key, value)
            return
        self.s.set(key, value)
        self.s.save()

    def pending_value(self, key):
        """What a key would become if Apply were pressed."""
        if key in self._pending:
            return self._pending[key][1]
        return self.s.get(key)

    # ---- sections ------------------------------------------------------

    def _audio_section(self):
        self._profiles_section()
        self.form.addWidget(heading("Audio"))
        self.form.addWidget(hint(
            "Pick the output you want to listen in on. Leave it on the "
            "Windows default if you are not sure."))
        rows = self._rows()

        self.device = QComboBox()
        self._fill_devices()

        want = (self.s.get("audio.device_name", "") or "",
                self.s.get("audio.device_kind", "output") or "output")
        if want[1] == "process":
            want = (self.s.get("audio.process_exe", ""), "process")
        idx = index_of(self.device, want)
        self.device.setCurrentIndex(idx if idx >= 0 else 0)
        self.device.currentIndexChanged.connect(self._pick_device)
        # The capture picker is one control backed by several keys,
        # so every one of them restores the whole thing.
        for _k in ('audio.device_kind', 'audio.device_name',
                   'audio.process_exe', 'audio.process_name'):
            self._restorers[_k] = lambda _v: self._restore_device()
        self._widgets['audio.device_name'] = self.device

        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        pick_row.addWidget(self.device, 1)
        refresh_apps = QPushButton("Refresh apps")
        refresh_apps.setToolTip("Look again for running applications")
        refresh_apps.clicked.connect(self._refresh_apps)
        pick_row.addWidget(refresh_apps)
        rows.addRow("Listen to", pick_row)

        # --- how a clip is decided --------------------------------------
        self.mode = QComboBox()
        self.mode.addItem("Fixed clips on a timer", "cycle")
        self.mode.addItem("Record when it hears something", "activation")
        midx = self.mode.findData(self.s.get("audio.mode", "cycle"))
        self.mode.setCurrentIndex(midx if midx >= 0 else 0)
        self.mode.currentIndexChanged.connect(self._pick_mode)
        self._widgets['audio.mode'] = self.mode
        self._restorers['audio.mode'] = (
            lambda v, w=self.mode: w.setCurrentIndex(
                max(0, w.findData(v))))
        rows.addRow("Capture", self.mode)

        # Each group is kept so the other can be greyed out: showing
        # settings that currently do nothing is worse than hiding them,
        # because they look like they should work.
        self.cycle_rows = []
        self.activation_rows = []

        clip = self._bind_spin("audio.record_seconds", 3, 60, " s")
        cycle = self._bind_spin("audio.cycle_seconds", 5, 300, " s")
        self.cycle_rows += [self._labelled(rows, "Clip length", clip),
                            self._labelled(rows, "Cycle length", cycle)]

        start_at = self._bind_spin("audio.activation_rms", 0, 5000, "", 50)
        quiet = self._bind_double("audio.activation_silence", 0.3, 15.0, 0.5)
        longest = self._bind_spin("audio.activation_max", 3, 300, " s")
        shortest = self._bind_double("audio.activation_min", 0.2, 10.0, 0.5)
        self.activation_rows += [
            self._labelled(rows, "Starts at level", start_at),
            self._labelled(rows, "Stops after quiet", quiet),
            self._labelled(rows, "Longest clip", longest),
            self._labelled(rows, "Ignore shorter than", shortest),
        ]

        rows.addRow("Silence cutoff",
                    self._bind_spin("audio.silence_rms", 0, 5000, "", 50))
        self.device_note = QLabel("")
        self.device_note.setObjectName("fieldLabel")
        self.device_note.setWordWrap(True)
        self.form.addWidget(self.device_note)
        self.mode_note = QLabel("")
        self.mode_note.setObjectName("fieldLabel")
        self.mode_note.setWordWrap(True)
        self.form.addWidget(self.mode_note)
        self._apply_mode()
        self.form.addWidget(hint(
            "Cycle length is measured from the start of one clip to the "
            "start of the next, and applies to fixed clips only. Sound "
            "activated capture listens again as soon as the last clip is "
            "dealt with."))

    def _labelled(self, rows, text, field):
        """Add a row and hand back both halves, so both can be greyed."""
        label = QLabel(text)
        rows.addRow(label, field)
        return (label, field)

    def _pick_mode(self, _i):
        """
        Switch between fixed clips and sound-activated capture.

        Only one set of settings applies at a time, so the other is
        disabled rather than left looking adjustable.
        """
        mode = self.mode.currentData() or "cycle"
        self._save("audio.mode", mode)
        self._apply_mode()
        if self._loading:
            return
        if mode == "activation":
            self.mode_note.setText(
                "Recording starts when the level passes the trigger and "
                "runs until it has been quiet for long enough, or the "
                "longest clip is reached. The meter on the Live page "
                "shows the level as it happens.")
        else:
            self.mode_note.setText(
                "A clip of fixed length is taken every cycle, whether or "
                "not anything was said.")

    def _apply_mode(self):
        active = (self.mode.currentData() or "cycle") == "activation"
        for label, field in self.cycle_rows:
            label.setEnabled(not active)
            field.setEnabled(not active)
        for label, field in self.activation_rows:
            label.setEnabled(active)
            field.setEnabled(active)

    def _fill_devices(self):
        """
        Populate the capture picker: outputs, then microphones, then apps.

        One grouped list rather than separate controls. Two pickers would
        mean two places to look and a third setting to say which one wins.
        """
        self.device.addItem("Windows default output", ("", "output"))
        try:
            from avcore.audio import list_devices
            devices = list_devices()
            outputs = [d for d in devices if d["kind"] == "output"]
            inputs = [d for d in devices if d["kind"] == "input"]

            for d in outputs:
                mark = "  (default)" if d.get("is_default") else ""
                self.device.addItem(f"Listen in on:  {d['name']}{mark}",
                                    (d["name"], "output"))
            if inputs:
                self.device.insertSeparator(self.device.count())
                self.device.addItem("Default microphone", ("", "input"))
                for d in inputs:
                    mark = "  (default)" if d.get("is_default") else ""
                    self.device.addItem(
                        f"Microphone:  {d['name']}{mark}",
                        (d["name"], "input"))
        except Exception as exc:
            self.device.addItem(f"Could not list devices: {exc}",
                                ("", "output"))

        # Applications last: it is the specialist option, and what is
        # running changes constantly, hence the refresh button beside it.
        self._add_applications()

    def _add_applications(self):
        """
        Add running applications to the picker.

        Only programs that have opened an audio stream appear - a list of
        every process would be unusable, and one that has never made a
        sound cannot be captured anyway.
        """
        try:
            from avcore.process_audio import audible_processes, is_supported
        except ImportError:
            return

        ok, why = is_supported()
        if not ok:
            self.device.insertSeparator(self.device.count())
            self.device.addItem(f"Per-app capture unavailable: {why}",
                                ("", "unavailable"))
            return

        apps = audible_processes()
        saved = self.s.get("audio.process_exe", "")
        if not apps and not saved:
            return

        self.device.insertSeparator(self.device.count())
        seen = set()
        for app in apps:
            seen.add(app["exe"].lower())
            mark = "  (playing)" if app["playing"] else ""
            self.device.addItem(f"App:  {app['name']}{mark}",
                                (app["exe"], "process"))

        # A saved target that is not running right now still belongs in
        # the list, or selecting it would silently reset to something else
        # the next time Settings is opened.
        if saved and saved.lower() not in seen:
            label = self.s.get("audio.process_name") or saved
            self.device.addItem(f"App:  {label}  (not running)",
                                (saved, "process"))

    def _refresh_apps(self):
        """Rebuild the picker, keeping the current choice selected."""
        current = self.device.currentData()
        self.device.blockSignals(True)
        self.device.clear()
        self._fill_devices()
        idx = index_of(self.device, current)
        self.device.setCurrentIndex(idx if idx >= 0 else 0)
        self.device.blockSignals(False)
        if idx < 0 and isinstance(current, tuple) and current[1] == "process":
            self.device_note.setText(
                "That application is no longer running.")
        else:
            self.device_note.setText("")

    def _update_keep_note(self):
        """
        Spell out that the limit ignores favourites.

        The count is included because an abstract promise is easy to
        distrust - seeing "your 5 favourites are kept as well" says it
        about the actual gallery rather than in principle.
        """
        catalog = getattr(self.engine, "catalog", None)
        kept = len(catalog.favourites()) if catalog is not None else 0
        self._fav_count = kept

        text = ("Counts only images you have not favourited. Older ones "
                "past this number are deleted from disk when a new image "
                "is made.")
        if kept:
            text += (f" Your {kept} favourite{'s' if kept != 1 else ''} "
                     f"{'are' if kept != 1 else 'is'} kept as well and "
                     f"{'do' if kept != 1 else 'does'} not count towards "
                     f"the limit.")
        else:
            text += " Favourites are never deleted and never counted."
        self.keep_note.setText(text)

    def _restore_device(self):
        """Put the capture picker back to what the settings say."""
        kind = self.s.get('audio.device_kind', 'output')
        if kind == 'process':
            want = (self.s.get('audio.process_exe', ''), 'process')
        else:
            want = (self.s.get('audio.device_name', '') or '', kind)
        idx = index_of(self.device, want)
        self.device.setCurrentIndex(idx if idx >= 0 else 0)

    def _pick_device(self, _i):
        """
        Change the capture source without a restart.

        The engine rebuilds its recorder between cycles, so switching takes
        effect on the next capture rather than needing the app closed and
        reopened.
        """
        data = self.device.currentData()
        if not isinstance(data, tuple):
            return          # a separator or the error placeholder
        name, kind = data
        if kind == "unavailable":
            return

        if kind == "process":
            label = self.device.currentText().replace("App:", "").strip()
            label = label.replace("(playing)", "").replace(
                "(not running)", "").strip()
            self._save("audio.process_exe", name)
            self._save("audio.process_name", label)
            self._save("audio.device_kind", "process")
        else:
            self._save("audio.device_name", name)
            self._save("audio.device_kind", kind)

        if self._loading:
            return
        if self.confirm_mode():
            # The device is not touched until Apply; saying so is better
            # than silently doing nothing visible.
            self.device_note.setText("Will change when you apply.")
            return
        reload_audio = getattr(self.engine, "reload_audio", None)
        if reload_audio:
            reload_audio()
            if kind == "process":
                self.device_note.setText(
                    "Capturing that application only, from the next clip. "
                    "It has to be playing something to be heard.")
            else:
                what = "microphone" if kind == "input" else "output"
                self.device_note.setText(
                    f"Now capturing from that {what}, from the next clip.")

    def _speech_section(self):
        self.form.addWidget(heading("Speech"))
        rows = self._rows()

        self.model = QComboBox()
        self.model.addItems(SPEECH_MODELS)
        cur = self.s.get("speech.model", "small.en")
        if cur in SPEECH_MODELS:
            self.model.setCurrentText(cur)
        self.model.currentTextChanged.connect(
            lambda v: self._save("speech.model", v))
        self._widgets['speech.model'] = self.model
        self._restorers['speech.model'] = (
            lambda v, w=self.model: w.setCurrentText(str(v)))
        rows.addRow("Model", self.model)
        rows.addRow("Fewest words",
                    self._bind_spin("speech.min_words", 1, 20))
        rows.addRow("Prompt length cap",
                    self._bind_spin("speech.max_prompt_chars", 50, 2000,
                                    "", 50))
        self.form.addWidget(hint(
            "Bigger models are more accurate and slower. Changing this "
            "takes effect when the app restarts."))

    def _image_section(self):
        self.form.addWidget(heading("Images"))
        rows = self._rows()

        self.backend = QComboBox()
        self.backend.addItem("ComfyUI (local, on your GPU)", "comfyui")
        self.backend.addItem("Pollinations (online, watermarked)",
                             "pollinations")
        bidx = self.backend.findData(self.s.get("image.backend", "comfyui"))
        self.backend.setCurrentIndex(bidx if bidx >= 0 else 0)
        self.backend.currentIndexChanged.connect(
            lambda _i: self._save("image.backend",
                                  self.backend.currentData()))
        self._widgets['image.backend'] = self.backend
        self._restorers['image.backend'] = (
            lambda v, w=self.backend: w.setCurrentIndex(
                max(0, w.findData(v))))
        rows.addRow("Generate with", self.backend)

        # Which installed model to use, right under the choice of how.
        # It lists only what is actually on disk: offering a model that
        # is not there just produces a failure at the first generation.
        self.model_pick = QComboBox()
        self.model_pick.currentIndexChanged.connect(self._pick_model)
        rows.addRow("Model", self.model_pick)
        # A form layout's addRow hands back nothing, so the label is
        # fetched separately - hiding the field without its label leaves
        # a caption pointing at empty space.
        self.model_row = [self.model_pick,
                          rows.labelForField(self.model_pick)]
        self._reload_models()

        self.backend.currentIndexChanged.connect(
            lambda _i: self._sync_backend_rows())

        size = QHBoxLayout()
        self.width = self._make_spin("image.width", 256, 2048, "", 64, 92)
        self.height = self._make_spin("image.height", 256, 2048, "", 64, 92)
        size.addWidget(self.width)
        size.addWidget(QLabel("x"))
        size.addWidget(self.height)
        size.addStretch(1)
        rows.addRow("Size", size)

        self.suffix = self._bind_text(
            "image.style_suffix", "added to the end of every prompt")
        self.suffix.textChanged.connect(self._check_suffix)
        rows.addRow("Style", self.suffix)
        rows.addRow("Avoid", self._bind_text("image.negative_prompt"))

        # The safety terms are added when a picture is generated, not
        # written into the field, so that turning safe mode off gives
        # back exactly the wording somebody chose. That is the right
        # behaviour and completely invisible, which is its own fault -
        # this line says what is actually being sent.
        self.avoid_note = QLabel("")
        self.avoid_note.setObjectName("fieldLabel")
        self.avoid_note.setWordWrap(True)
        rows.addRow("", self.avoid_note)
        rows.addRow("Keep last",
                    self._bind_spin("image.keep_images", 0, 500, " images",
                                    10))
        # Said plainly, because the behaviour is invisible otherwise: the
        # number counts only ordinary images, and a favourite is never
        # deleted no matter how low it is set.
        self.keep_note = hint("")
        self.form.addWidget(self.keep_note)
        self._update_keep_note()
        self.form.addWidget(
            self._bind_check("image.manual_auto_push",
                             "Send typed prompts to the overlay right away"))

        self.suffix_warning = QLabel("")
        self.suffix_warning.setObjectName("errorText")
        self.suffix_warning.setWordWrap(True)
        self.suffix_warning.hide()
        self.form.addWidget(self.suffix_warning)
        self._check_suffix()

        self.form.addWidget(hint(
            "Below about 1 megapixel, SDXL starts drawing anatomy badly, and "
            "a full 1344x768 render costs barely more than a small one."))

    # Heuristic only, and deliberately short. The point is to catch the
    # obvious case and say something useful once, not to police wording.
    _EXPLICIT_HINTS = (
        "nsfw", "nude", "naked", "topless", "porn", "explicit", "erotic",
        "sexy", "sexual", "lingerie", "hentai",
    )

    def _check_suffix(self):
        if not self.s.get("ui.warn_on_explicit_suffix", True):
            self.suffix_warning.hide()
            return
        text = (self.suffix.text() or "").lower()
        if not any(h in text for h in self._EXPLICIT_HINTS):
            self.suffix_warning.hide()
            return
        self.suffix_warning.setText(
            "This style is appended to whatever the microphone picks up. "
            "Live speech often contains real people's names, so the result "
            "can be a sexual image labelled with someone who never agreed "
            "to it - worth keeping in mind before streaming this or handing "
            "a build to a friend. Manual prompts are unaffected. You can "
            "turn this notice off under Interface.")
        self.suffix_warning.show()

    def _comfy_section(self):
        # Everything this section adds is remembered so it can be
        # hidden as a block when generation is not local. Recorded by
        # position rather than by name: the section builds a mix of
        # widgets and layouts, and listing them by hand would drift the
        # first time a row was added.
        comfy_from = self.form.count()
        self.form.addWidget(heading("ComfyUI"))
        rows = self._rows()

        self.checkpoint = QComboBox()
        self.checkpoint.setEditable(False)
        self._reload_checkpoints()
        self.checkpoint.currentIndexChanged.connect(self._pick_checkpoint)
        self._widgets['comfyui.checkpoint'] = self.checkpoint
        self._restorers['comfyui.checkpoint'] = (
            lambda v, w=self.checkpoint: w.setCurrentIndex(
                max(0, index_of(w, v or ''))))

        ck_row = QHBoxLayout()
        ck_row.addWidget(self.checkpoint, 1)
        # Beside the picker, because this is where someone already comes
        # to choose a model - finding out you can get more belongs in
        # the same place as discovering you have only one.
        more = QPushButton("Get more models")
        more.setToolTip("Browse and download models from Civitai")
        more.clicked.connect(self._browse_models)
        ck_row.addWidget(more)

        # Beside the models button rather than in a section of its own:
        # they are the same activity, and a LoRA is only interesting to
        # somebody already thinking about which model to use.
        loras = QPushButton("LoRAs")
        loras.setToolTip("Browse and download LoRAs from Civitai")
        loras.clicked.connect(self._browse_loras)
        ck_row.addWidget(loras)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._reload_checkpoints)
        ck_row.addWidget(refresh)
        rows.addRow("Model", ck_row)

        rows.addRow("Steps", self._bind_spin("comfyui.steps", 1, 100))
        rows.addRow("Guidance", self._bind_double("comfyui.cfg", 0.0, 30.0))
        rows.addRow("Address", self._bind_text("comfyui.url"))
        rows.addRow("Give up after",
                    self._bind_spin("comfyui.timeout", 30, 900, " s", 10))
        self.form.addWidget(
            self._bind_check("comfyui.autostart",
                             "Start ComfyUI with the app"))

        self.comfy_items = [self.form.itemAt(i)
                            for i in range(comfy_from, self.form.count())]
        # Applied now that both halves exist, so a panel opened while
        # set to online does not start out showing them.
        self._safety_section()
        # The Avoid row is built before this section exists, so its note
        # is filled in once both are there - otherwise opening Settings
        # with safe mode already on shows nothing until the tick is
        # touched.
        self._sync_avoid_note()
        self._lora_section()
        self._sync_backend_rows()

    def _profiles_section(self):
        """
        Saved sets of preferences, and the way back to the defaults.

        At the top because it is the shortcut past everything below -
        putting it at the bottom would mean scrolling past the forty
        settings it exists to save you from.
        """
        self.form.addWidget(heading("Profiles"))
        note = QLabel(
            "Save the settings you like under a name and come back to "
            "them later. Folder locations and your API key are not part "
            "of a profile.")
        note.setObjectName("fieldLabel")
        note.setWordWrap(True)
        self.form.addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.profile_pick = QComboBox()
        self.profile_pick.setMinimumWidth(200)
        self.profile_pick.currentIndexChanged.connect(
            lambda _i: self._sync_profiles())
        row.addWidget(self.profile_pick, 1)

        self.profile_load = QPushButton("Load")
        self.profile_load.clicked.connect(self._load_profile)
        row.addWidget(self.profile_load)

        save_btn = QPushButton("Save current as...")
        save_btn.clicked.connect(self._save_profile)
        row.addWidget(save_btn)

        self.profile_delete = QPushButton("Delete")
        self.profile_delete.setObjectName("denyButton")
        self.profile_delete.clicked.connect(self._delete_profile)
        row.addWidget(self.profile_delete)

        self.form.addLayout(row)

        reset_row = QHBoxLayout()
        self.profile_note = QLabel("")
        self.profile_note.setObjectName("fieldLabel")
        self.profile_note.setWordWrap(True)
        reset_row.addWidget(self.profile_note, 1)
        reset_btn = QPushButton("Reset to defaults")
        reset_btn.setObjectName("denyButton")
        reset_btn.setToolTip(
            "Put every setting back to how the app ships")
        reset_btn.clicked.connect(self._reset_settings)
        reset_row.addWidget(reset_btn)
        self.form.addLayout(reset_row)

        self._reload_profiles()

    def _reload_profiles(self):
        from avcore import profiles

        self.profile_pick.blockSignals(True)
        self.profile_pick.clear()
        saved = profiles.names()
        for name in saved:
            self.profile_pick.addItem(name, name)
        if not saved:
            self.profile_pick.addItem("No profiles saved yet", "")
        self.profile_pick.blockSignals(False)
        self._sync_profiles()

    def _sync_profiles(self):
        has = bool(self.profile_pick.currentData())
        self.profile_load.setEnabled(has)
        self.profile_delete.setEnabled(has)

    def _save_profile(self):
        from avcore import profiles

        from .dialogs import NameProfile

        # Anything held back by the confirm bar is not in the settings
        # yet, so saving now would quietly store the old values.
        if self.is_dirty():
            self.profile_note.setText(
                "Apply or cancel your pending changes first - a profile "
                "saves what is in effect, not what is waiting.")
            return

        dialog = NameProfile(profiles.names(),
                             self.profile_pick.currentData() or "", self)
        if dialog.exec() != QDialog.Accepted:
            return
        name = dialog.chosen_name()
        try:
            profiles.save(name, self.s)
        except (OSError, ValueError) as exc:
            self.profile_note.setText(f"Could not save: {exc}")
            return
        self._reload_profiles()
        index = self.profile_pick.findData(name)
        if index >= 0:
            self.profile_pick.setCurrentIndex(index)
        self.profile_note.setText(f"Saved {name}.")

    def _load_profile(self):
        from avcore import profiles

        from .dialogs import ConfirmProfile

        name = self.profile_pick.currentData()
        if not name:
            return
        if ConfirmProfile(name, self).exec() != QDialog.Accepted:
            return
        try:
            applied = profiles.apply(name, self.s)
        except (KeyError, OSError) as exc:
            self.profile_note.setText(f"Could not load {name}: {exc}")
            return
        self._adopt_settings()
        self.profile_note.setText(
            f"Loaded {name} - {applied} settings applied.")

    def _delete_profile(self):
        from avcore import profiles

        from .dialogs import ConfirmProfileDelete

        name = self.profile_pick.currentData()
        if not name:
            return
        if ConfirmProfileDelete(name, self).exec() != QDialog.Accepted:
            return
        profiles.delete(name)
        self._reload_profiles()
        self.profile_note.setText(f"Deleted {name}.")

    def _reset_settings(self):
        from avcore import profiles

        from .dialogs import ConfirmReset

        if ConfirmReset(self).exec() != QDialog.Accepted:
            return
        changed = profiles.reset_to_defaults(self.s)
        self._adopt_settings()
        self.profile_note.setText(
            f"Reset to defaults - {changed} settings changed."
            if changed else "Everything was already at its default.")

    def _adopt_settings(self):
        """
        Redraw every field from the settings, and drop pending edits.

        Loading a profile or resetting changes the settings underneath
        the panel, so anything the confirm bar was holding refers to a
        world that no longer exists.
        """
        # _loading is what stops the field handlers recording these as
        # edits; blocking signals per widget would miss the ones that
        # react through other paths.
        was, self._loading = self._loading, True
        try:
            for key in self._widgets:
                restore = self._restorers.get(key)
                if restore is not None:
                    restore(self.s.get(key))
        finally:
            self._loading = was

        self._pending.clear()
        self.dirty_changed.emit(False)

        # The same follow-ups cancelling makes: several controls grey
        # themselves from the live values rather than from their own.
        self._apply_mode()
        self._check_suffix()
        self._sync_backend_rows()
        self._reload_models()
        self._sync_safe()
        self._sync_avoid_note()
        self._run_side_effects(set(self._widgets))

    def _reload_models(self):
        """
        Fill the model list from what setup has installed.

        Deliberately the tier models rather than every checkpoint on
        disk: this row answers "which of the two did I install", and the
        full list - including anything added from the catalogue - is the
        picker down in the ComfyUI section.
        """
        from avcore.setup import TIERS, models_for_tier

        self.model_pick.blockSignals(True)
        self.model_pick.clear()

        # Each option carries the file that really satisfies it, not the
        # official filename. Writing the official name would point the
        # app at a checkpoint that is not on this machine - almost
        # nobody's SDXL model is called sd_xl_base_1.0 - and generation
        # would quietly fall back to whatever was found first.
        here = []
        for key, spec in TIERS.items():
            models = models_for_tier(key, self.s)
            if not models:
                continue
            here.append(key)
            label = spec["label"]
            if models[0].name != spec["name"]:
                label = f"{spec['label']}  ({models[0].name})"
            self.model_pick.addItem(label, models[0].name)

        if not here:
            self.model_pick.addItem("None installed yet", "")

        current = (self.s.get("comfyui.checkpoint") or "").strip()
        index = self.model_pick.findData(current)
        self.model_pick.setCurrentIndex(index if index >= 0 else 0)
        self.model_pick.setEnabled(bool(here))
        self.model_pick.blockSignals(False)

    def _pick_model(self, _index):
        name = self.model_pick.currentData() or ""
        if name:
            self._save("comfyui.checkpoint", name)

    def _sync_backend_rows(self):
        """
        Hide what the chosen backend makes irrelevant.

        Generating online uses no local model and no ComfyUI, so leaving
        those rows on screen invites fiddling with settings that cannot
        do anything.
        """
        local = (self.backend.currentData() or "comfyui") == "comfyui"
        for widget in getattr(self, "model_row", None) or []:
            if widget is not None:
                widget.setVisible(local)
        for item in getattr(self, "comfy_items", None) or []:
            _set_item_visible(item, local)

    def _reload_checkpoints(self):
        self.checkpoint.blockSignals(True)
        self.checkpoint.clear()
        self.checkpoint.addItem("First one found", "")
        try:
            from avcore.images import ComfyBackend
            names = ComfyBackend(self.s).list_checkpoints()
            for name in names:
                self.checkpoint.addItem(name, name)
            self._models_listed = bool(names)
        except Exception:
            self.checkpoint.addItem(
                "Starting ComfyUI - models will appear shortly", "")
            self._models_listed = False
        want = self.s.get("comfyui.checkpoint", "") or ""
        idx = self.checkpoint.findData(want)
        self.checkpoint.setCurrentIndex(idx if idx >= 0 else 0)
        self.checkpoint.blockSignals(False)

    def _browse_models(self):
        """
        Open the catalogue.

        The list is reloaded when it closes rather than while it is
        open: a model that has just arrived should be selectable
        straight away, and nothing else in here needs to react.
        """
        from .model_browser import ModelBrowser

        # Named, not positional. When the browser learned to show
        # LoRAs it gained a `kind` argument in second place, so this
        # call was handing it the panel as the kind - which failed on
        # kind.upper() inside a slot, where Qt swallows the error and
        # the button simply does nothing.
        browser = ModelBrowser(self.s, parent=self)
        browser.installed_changed.connect(self._reload_checkpoints)
        browser.exec()
        self._reload_checkpoints()

    def _safety_section(self):
        """
        One tick, and an honest account of what it does.

        The explanation matters as much as the control. Every layer
        behind it is leaky on its own, and somebody who believes this
        makes explicit output impossible will be more surprised than
        somebody who was told the truth.
        """
        self.form.addWidget(heading("Safe mode"))

        self.safe_tick = QCheckBox("Avoid explicit images")
        self.safe_tick.setChecked(
            bool(self.s.get("safety.safe_mode", False)))
        self.safe_tick.toggled.connect(self._toggle_safe)
        self.form.addWidget(self.safe_tick)

        self.form.addWidget(hint(
            "While this is on: safety terms are added to the negative "
            "prompt, prompts asking for explicit images are refused, "
            "models flagged as adult are hidden, every picture is "
            "checked, and anything flagged is blurred in the gallery "
            "and kept off the overlay."))
        self.form.addWidget(hint(
            "None of those is reliable on its own, and together they "
            "make explicit output unlikely rather than impossible. The "
            "model matters most: one merged for explicit output will "
            "defeat all of it."))

    def _sync_avoid_note(self):
        """Say what safe mode is adding, or nothing when it is off."""
        note = getattr(self, "avoid_note", None)
        if note is None:
            return
        from avcore.safety import NEGATIVE_TERMS

        if bool(self.s.get("safety.safe_mode", False)):
            note.setText(
                f"Safe mode also avoids: {NEGATIVE_TERMS}")
            note.show()
        else:
            note.clear()
            note.hide()

    def _sync_safe(self):
        """
        Show what the setting says, without firing the handler.

        The tick is built by hand rather than through the field helper,
        so the panel's own restore pass does not know about it - which
        left Settings showing safe mode off while the strip had just
        turned it on.
        """
        tick = getattr(self, "safe_tick", None)
        if tick is None:
            return
        wanted = bool(self.s.get("safety.safe_mode", False))
        if tick.isChecked() != wanted:
            tick.blockSignals(True)
            tick.setChecked(wanted)
            tick.blockSignals(False)

    def _toggle_safe(self, on):
        self._save("safety.safe_mode", bool(on))
        self._sync_avoid_note()
        window = self.window()
        sync = getattr(window, "_sync_quick", None)
        if sync is not None:
            sync()

    def _lora_section(self):
        """
        Which LoRAs apply, listed under the model that they sit on top of.

        Here rather than in a dialog because it is a standing choice: the
        model and its LoRAs are one decision, and splitting them across
        two windows makes it hard to see what will actually be used.
        """
        from .lora_chooser import LoraChooser

        self.form.addWidget(heading("LoRAs"))
        self.loras = LoraChooser(self.s)
        self.loras.changed.connect(self._loras_changed)
        self.form.addWidget(self.loras)

    def _loras_changed(self):
        """Tell the strips, which show the same choice in miniature."""
        window = self.window()
        sync = getattr(window, "_sync_quick", None)
        if sync is not None:
            sync()

    def _browse_loras(self):
        """
        The same browser, pointed at LoRAs.

        One dialog rather than two: the only differences are the word it
        searches Civitai for and the folder things land in, and a second
        copy would be a second place for a bug to live.
        """
        from .model_browser import ModelBrowser

        ModelBrowser(self.s, kind="LORA", parent=self).exec()

    def _pick_checkpoint(self, _i):
        self._save("comfyui.checkpoint", self.checkpoint.currentData() or "")

    def _paths_section(self):
        self.form.addWidget(heading("Folders"))
        rows = self._rows()
        rows.addRow("Typed prompts save to",
                    self._folder_row("paths.manual_dir"))
        rows.addRow("Overlay images",
                    self._folder_row("paths.overlay_dir"))
        rows.addRow("Overlay port",
                    self._bind_spin("server.port", 1024, 65535))
        self.form.addWidget(hint(
            "Changing the port takes effect when the app restarts, and the "
            "OBS source needs the new one."))

    def _folder_row(self, key):
        row = QHBoxLayout()
        field = self._bind_text(key)
        browse = QPushButton("Browse")

        def pick():
            start = str(self.s.get(key, "") or Path.home())
            chosen = QFileDialog.getExistingDirectory(
                self, "Choose a folder", start)
            if chosen:
                field.setText(chosen)
                self._save(key, chosen)

        browse.clicked.connect(pick)
        row.addWidget(field, 1)
        row.addWidget(browse)
        return row

    def _ui_section(self):
        self.form.addWidget(heading("Interface"))
        rows = self._rows()

        self.layout_pick = QComboBox()
        for value, label in LAYOUTS:
            self.layout_pick.addItem(label, value)
        lidx = self.layout_pick.findData(self.s.get("ui.layout", "hybrid"))
        self.layout_pick.setCurrentIndex(lidx if lidx >= 0 else 0)
        self.layout_pick.currentIndexChanged.connect(self._pick_layout)
        self._widgets['ui.layout'] = self.layout_pick
        self._restorers['ui.layout'] = (
            lambda v, w=self.layout_pick: w.setCurrentIndex(
                max(0, w.findData(v))))
        rows.addRow("Layout", self.layout_pick)

        self.form.addWidget(
            self._bind_check("ui.warn_on_explicit_suffix",
                             "Warn when a style could apply to real names"))
        self.form.addWidget(
            self._bind_check("ui.autostart_listening",
                             "Start listening as soon as the app opens"))
        self.confirm_check = self._bind_check(
            "ui.confirm_settings",
            "Ask before applying settings changes")
        self.form.addWidget(self.confirm_check)
        self.form.addWidget(hint(
            "With that on, changes wait for Apply and are discarded if the "
            "app closes first. With it off, every change takes effect the "
            "moment you make it. Layout changes wait for Apply too, so the "
            "window does not rearrange around a decision you have not made "
            "yet."))

    def _pick_layout(self, _i):
        value = self.layout_pick.currentData()
        self._save("ui.layout", value)
        if self._loading:
            return
        # In confirm mode the layout is left alone until Apply, or the
        # window would rearrange itself around a change not yet made.
        if not self.confirm_mode() and self.on_layout_change:
            self.on_layout_change(value)

    def on_state(self, snapshot):
        """
        Settings shows configuration, not live state - with one exception.

        The model list can only be read from a running ComfyUI, so when it
        comes up the list is filled in. Otherwise the panel would sit
        showing "starting..." until the user thought to press Refresh.
        """
        # Keyed on the readiness changing, not on whether the last
        # listing worked. Keying it on success meant that when ComfyUI
        # said it was ready but the listing failed - it is shutting
        # down, it is wedged, its port is bound by a dead process - the
        # panel retried on every snapshot. The engine publishes those
        # many times a second and each attempt blocks the interface
        # until the connection is refused, so the whole window froze.
        ready = bool(snapshot.get("comfy_ready"))
        if ready != getattr(self, "_comfy_was_ready", None):
            self._comfy_was_ready = ready
            self._reload_checkpoints()

        # Favourites are marked in the Gallery, so the count beside "keep
        # last" would otherwise go stale while this panel sits open.
        catalog = getattr(self.engine, "catalog", None)
        if catalog is not None:
            if len(catalog.favourites()) != getattr(self, "_fav_count", -1):
                self._update_keep_note()
