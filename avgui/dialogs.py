"""The confirmation dialogs, and the rename prompt."""

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QPropertyAnimation, Qt,
)
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFormLayout,
    QDialog, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from . import theme


class ConfirmDelete(QDialog):
    """
    Asks before destroying a file.

    Shows the image itself rather than only its filename: the filenames
    are near-identical timestamps, and confirming a deletion by reading
    "live_20260912_173134_006.png" is not really confirming anything.
    """

    def __init__(self, path, pixmap=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete image")
        self.setModal(True)
        self.setMinimumWidth(400)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel("Permanently delete this image?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        if pixmap is not None and not pixmap.isNull():
            thumb = QLabel()
            thumb.setObjectName("preview")
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setPixmap(pixmap.scaledToWidth(
                260, Qt.SmoothTransformation))
            outer.addWidget(thumb, 0, Qt.AlignHCenter)

        body = QLabel(
            f"{path.name} will be removed from the gallery and the file "
            f"deleted from disk. This cannot be undone.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)

        deny = QPushButton("Deny")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)

        confirm = QPushButton("Confirm")
        confirm.setObjectName("confirmButton")
        confirm.clicked.connect(self.accept)
        row.addWidget(confirm)
        outer.addLayout(row)

        # Deny has focus, so a stray Enter does not destroy anything.
        deny.setDefault(True)
        deny.setFocus()


class ConfirmUnfavourite(QDialog):
    """
    Asks before dropping a kept prompt.

    The same shape as the delete confirmation, because it answers the same
    question - are you sure you want to lose this - even though nothing on
    disk is destroyed here.
    """

    def __init__(self, prompt, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remove favourite")
        self.setModal(True)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel("Remove this prompt from favourites?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        quoted = QLabel(prompt)
        quoted.setObjectName("feed")
        quoted.setWordWrap(True)
        outer.addWidget(quoted)

        body = QLabel("It will be taken out of the favourites list. "
                      "Nothing on disk is affected.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)

        deny = QPushButton("Deny")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)

        confirm = QPushButton("Confirm")
        confirm.setObjectName("confirmButton")
        confirm.clicked.connect(self.accept)
        row.addWidget(confirm)
        outer.addLayout(row)

        # Deny has focus, matching the delete dialog: the safe option is
        # always the one Enter picks.
        deny.setDefault(True)
        deny.setFocus()


class ConfirmPrune(QDialog):
    """
    Asks before a lower "keep last" deletes images.

    Unlike the other confirmations this one is about files the user has
    not looked at - so it says plainly how many go, which are safe, and
    that the newest are the ones kept.
    """

    def __init__(self, count, keep, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete older images")
        self.setModal(True)
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        plural = "s" if count != 1 else ""
        title = QLabel(f"Delete {count} older image{plural}?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        body = QLabel(
            f"Keeping only the newest {keep} means {count} older "
            f"image{plural} will be removed from the gallery and deleted "
            f"from disk. Favourites are kept regardless and do not count "
            f"towards the limit.\n\nThis cannot be undone.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)

        deny = QPushButton("Deny")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)

        confirm = QPushButton("Confirm")
        confirm.setObjectName("confirmButton")
        confirm.clicked.connect(self.accept)
        row.addWidget(confirm)
        outer.addLayout(row)

        # Deny has focus, as everywhere else something is destroyed.
        deny.setDefault(True)
        deny.setFocus()


class RenameImage(QDialog):
    """
    Asks for a new name for an image.

    The extension is shown but not editable: changing it would not convert
    anything, it would only mislabel the file.
    """

    def __init__(self, path, pixmap=None, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("Rename image")
        self.setModal(True)
        self.setMinimumWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel("Rename this image")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        if pixmap is not None and not pixmap.isNull():
            thumb = QLabel()
            thumb.setObjectName("preview")
            thumb.setAlignment(Qt.AlignCenter)
            thumb.setPixmap(pixmap.scaledToWidth(220,
                                                 Qt.SmoothTransformation))
            outer.addWidget(thumb, 0, Qt.AlignHCenter)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.field = QLineEdit(path.stem)
        self.field.selectAll()
        self.field.returnPressed.connect(self.accept)
        row.addWidget(self.field, 1)
        suffix = QLabel(path.suffix)
        suffix.setObjectName("fieldLabel")
        row.addWidget(suffix)
        outer.addLayout(row)

        note = QLabel("The file is renamed on disk as well. Its prompt and "
                      "favourite mark travel with it.")
        note.setWordWrap(True)
        note.setObjectName("fieldLabel")
        outer.addWidget(note)

        self.error = QLabel("")
        self.error.setObjectName("errorText")
        self.error.setWordWrap(True)
        self.error.hide()
        outer.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)

        self.ok = QPushButton("Rename")
        self.ok.setObjectName("confirmButton")
        self.ok.clicked.connect(self.accept)
        buttons.addWidget(self.ok)
        outer.addLayout(buttons)

        self.field.setFocus()

    def new_stem(self):
        return self.field.text().strip()

    def show_error(self, message):
        """Keep the dialog open and say what was wrong."""
        self.error.setText(message)
        self.error.setVisible(bool(message))
        self.field.setFocus()
        self.field.selectAll()


class StyleEditor(QDialog):
    """
    Make or edit a style preset.

    Carries the same accent glow the change bar uses, so a window that
    appeared on purpose looks like it did. Frameless would fit the app
    better but loses the system move and close, which is a bad trade for
    a dialog people will drag around.
    """

    def __init__(self, presets, original=None, parent=None):
        super().__init__(parent)
        self.presets = presets
        self.original = original
        self.setWindowTitle("Edit style" if original else "New style")
        self.setModal(True)
        self.setMinimumWidth(460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(10)

        title = QLabel("Edit this style" if original else "Make a style")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        outer.addWidget(self._hint(
            "A style is added to the end of every prompt. Describe the "
            "look you want, not the subject - the subject comes from what "
            "you say or type."))

        outer.addWidget(self._label("Name"))
        self.name_box = QLineEdit(original["name"] if original else "")
        self.name_box.setPlaceholderText("Oil painting")
        self.name_box.textChanged.connect(self._revalidate)
        outer.addWidget(self.name_box)

        outer.addWidget(self._label("Style"))
        self.suffix_box = QPlainTextEdit(
            original["suffix"] if original else "")
        self.suffix_box.setPlaceholderText(
            "thick oil paint, visible brush strokes, warm light")
        self.suffix_box.setFixedHeight(80)
        self.suffix_box.textChanged.connect(self._preview)
        outer.addWidget(self.suffix_box)

        outer.addWidget(self._label("Preview"))
        self.preview = QLabel("")
        self.preview.setObjectName("feed")
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(46)
        outer.addWidget(self.preview)

        self.error = QLabel("")
        self.error.setObjectName("errorText")
        self.error.setWordWrap(True)
        self.error.hide()
        outer.addWidget(self.error)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok = QPushButton("Save style")
        self.ok.setObjectName("confirmButton")
        self.ok.clicked.connect(self._accept)
        row.addWidget(self.ok)
        outer.addLayout(row)

        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(0)
        self._glow.setColor(QColor(theme.ACCENT))
        self.setGraphicsEffect(self._glow)

        self._preview()
        self._revalidate()
        self.name_box.setFocus()

    @staticmethod
    def _label(text):
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    @staticmethod
    def _hint(text):
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        lbl.setWordWrap(True)
        return lbl

    def showEvent(self, event):
        super().showEvent(event)
        # The glow rises as the window appears, rather than being on from
        # the first frame, which would just look like a border.
        anim = QPropertyAnimation(self._glow, b"blurRadius", self)
        anim.setDuration(320)
        anim.setStartValue(0)
        anim.setEndValue(34)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        self._rise = anim
        anim.start(QAbstractAnimation.DeleteWhenStopped)

    def _preview(self):
        suffix = " ".join(self.suffix_box.toPlainText().split())
        example = "a lighthouse over a cold grey sea"
        self.preview.setText(f"{example}, {suffix}" if suffix else example)

    def _revalidate(self):
        problem = self.presets.validate(
            self.name_box.text(),
            self.original["name"] if self.original else None)
        self.error.setText(problem)
        self.error.setVisible(bool(problem))
        self.ok.setEnabled(not problem)

    def _accept(self):
        ok, message = self.presets.save(
            self.name_box.text(),
            self.suffix_box.toPlainText(),
            original=self.original["name"] if self.original else None)
        if ok:
            self.saved_name = message
            self.accept()
            return
        self.error.setText(message)
        self.error.setVisible(True)


class ConfirmPurge(QDialog):
    """
    Asks before emptying the gallery.

    The most destructive thing in the app, so it states the two numbers
    that matter - how many go and how many are safe - rather than a vague
    warning. Deny is the default, as everywhere something is destroyed.
    """

    def __init__(self, doomed, favourites, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Purge images")
        self.setModal(True)
        self.setMinimumWidth(460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        plural = "s" if doomed != 1 else ""
        title = QLabel(f"Delete {doomed} image{plural}?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        if favourites:
            safe = (f"Your {favourites} favourite"
                    f"{'s' if favourites != 1 else ''} "
                    f"{'are' if favourites != 1 else 'is'} kept.")
        else:
            safe = ("You have no favourites, so the gallery will be "
                    "emptied completely.")

        body = QLabel(
            f"Everything in the gallery that you have not favourited "
            f"will be removed from the gallery and deleted from disk. "
            f"{safe}\n\nThis cannot be undone.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)

        deny = QPushButton("Deny")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)

        confirm = QPushButton(f"Delete {doomed} image{plural}")
        confirm.setObjectName("confirmButton")
        confirm.clicked.connect(self.accept)
        row.addWidget(confirm)
        outer.addLayout(row)

        # The safe option has focus, so Enter cannot empty the gallery.
        deny.setDefault(True)
        deny.setFocus()


class NameProfile(QDialog):
    """
    Asks what to call a profile, and warns before replacing one.

    The warning matters: overwriting silently is how somebody loses the
    setup they spent an evening getting right.
    """

    def __init__(self, existing, suggested="", parent=None):
        super().__init__(parent)
        self.existing = {name.lower() for name in existing}
        self.setWindowTitle("Save settings profile")
        self.setModal(True)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(10)

        title = QLabel("Save these settings as")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        self.name = QLineEdit(suggested)
        self.name.setPlaceholderText("Streaming, Quiet room, Testing...")
        self.name.textChanged.connect(self._sync)
        outer.addWidget(self.name)

        self.note = QLabel(
            "Audio, speech, image and appearance settings are saved. "
            "Folder locations and your API key are not.")
        self.note.setObjectName("fieldLabel")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("denyButton")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.go = QPushButton("Save")
        self.go.setObjectName("confirmButton")
        self.go.clicked.connect(self.accept)
        row.addWidget(self.go)
        outer.addLayout(row)

        self.name.setFocus()
        self._sync()

    def _sync(self):
        chosen = self.name.text().strip()
        self.go.setEnabled(bool(chosen))
        if chosen.lower() in self.existing:
            self.go.setText("Replace")
            self.note.setText(
                f"A profile called {chosen} already exists. Saving will "
                f"replace it.")
        else:
            self.go.setText("Save")
            self.note.setText(
                "Audio, speech, image and appearance settings are saved. "
                "Folder locations and your API key are not.")

    def chosen_name(self):
        return self.name.text().strip()


class ConfirmProfile(QDialog):
    """Asks before replacing the current settings with a profile."""

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load profile")
        self.setModal(True)
        self.setMinimumWidth(430)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel(f"Load {name}?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        body = QLabel(
            "This replaces your current audio, speech, image and "
            "appearance settings with the ones saved in this profile.\n\n"
            "Anything you have not saved to a profile will be lost. Your "
            "folders, models and images are not affected.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.addStretch(1)
        deny = QPushButton("Cancel")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)
        go = QPushButton(f"Load {name}")
        go.setObjectName("confirmButton")
        go.clicked.connect(self.accept)
        row.addWidget(go)
        outer.addLayout(row)
        deny.setDefault(True)
        deny.setFocus()


class ConfirmReset(QDialog):
    """
    Asks before putting everything back to how it shipped.

    Says what survives as well as what goes: the worry with a reset
    button is usually "will I lose my pictures", and the answer is no.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reset settings")
        self.setModal(True)
        self.setMinimumWidth(450)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel("Reset all settings to defaults?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        body = QLabel(
            "Every setting goes back to how the app ships: audio, "
            "speech, image size, appearance, themes, all of it.\n\n"
            "Your images, favourites, models and saved profiles are "
            "kept, and so is where ComfyUI is installed. This cannot be "
            "undone - if there is a setup you want to keep, save it as a "
            "profile first.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.addStretch(1)
        deny = QPushButton("Keep my settings")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)
        go = QPushButton("Reset everything")
        go.setObjectName("confirmButton")
        go.clicked.connect(self.accept)
        row.addWidget(go)
        outer.addLayout(row)
        deny.setDefault(True)
        deny.setFocus()


class ConfirmProfileDelete(QDialog):
    """
    Asks before removing a saved profile.

    Its own dialog rather than the one used for images: that one shows
    the picture being deleted, which is the whole point of it and
    meaningless here.
    """

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete profile")
        self.setModal(True)
        self.setMinimumWidth(400)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel(f"Delete the profile {name}?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        body = QLabel(
            "The saved settings in it are lost. Your current settings "
            "stay exactly as they are - this only removes the saved "
            "copy.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.addStretch(1)
        deny = QPushButton("Keep it")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)
        go = QPushButton("Delete")
        go.setObjectName("confirmButton")
        go.clicked.connect(self.accept)
        row.addWidget(go)
        outer.addLayout(row)
        deny.setDefault(True)
        deny.setFocus()


class ConfirmAnimate(QDialog):
    """
    Asks what sort of clip to make, and what it will cost.

    The lengths and their sizes come from what this card actually
    managed, not from what sounds reasonable: a longer clip only fits if
    each frame is smaller, and asking for ten seconds at full size takes
    ComfyUI down rather than merely running slowly.
    """

    def __init__(self, name, shape="landscape", settings=None,
                 parent=None):
        super().__init__(parent)
        from avcore.video import LENGTHS, SHAPE_NAMES, plan

        self._plan = plan
        self._settings = settings
        self.setWindowTitle("Animate image")
        self.setModal(True)
        self.setMinimumWidth(480)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(10)

        title = QLabel(f"Make a clip from {name}")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        rows = QFormLayout()
        rows.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rows.setHorizontalSpacing(12)
        rows.setVerticalSpacing(8)

        self.length = QComboBox()
        for frames, _seconds, label, _estimate in LENGTHS:
            self.length.addItem(label, frames)
        self.length.currentIndexChanged.connect(self._retell)
        rows.addRow("Length", self.length)

        self.shape = QComboBox()
        for key, label in SHAPE_NAMES:
            self.shape.addItem(label, key)
        index = self.shape.findData(shape)
        self.shape.setCurrentIndex(index if index >= 0 else 0)
        self.shape.currentIndexChanged.connect(self._retell)
        # Defaulted from the picture, but not forced: cropping a
        # landscape photo to a portrait clip is a legitimate thing to
        # want.
        self.shape.setToolTip(
            "The picture is cropped to this shape. Chosen to match it, "
            "but you can pick another.")
        rows.addRow("Shape", self.shape)
        outer.addLayout(rows)

        self.detail = QLabel("")
        self.detail.setObjectName("fieldLabel")
        self.detail.setWordWrap(True)
        outer.addWidget(self.detail)

        note = QLabel(
            "Longer clips are made smaller so they fit in memory - every "
            "option takes about the same time.\n\n"
            "There is no sound: nothing that generates audio fits this "
            "graphics card. A prompt cannot steer it either - this model "
            "takes the picture and nothing else.\n\n"
            "ComfyUI does one job at a time, so anything the live page "
            "hears while this runs is queued and generated afterwards.")
        note.setWordWrap(True)
        note.setObjectName("fieldLabel")
        outer.addWidget(note)

        row = QHBoxLayout()
        row.addStretch(1)
        deny = QPushButton("Not now")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)
        self.go = QPushButton("Make the clip")
        self.go.setObjectName("confirmButton")
        self.go.clicked.connect(self.accept)
        row.addWidget(self.go)
        outer.addLayout(row)
        deny.setDefault(True)
        deny.setFocus()
        self._retell()

    def _retell(self):
        """Say what the current choice will produce, and how long it takes."""
        frames = self.length.currentData() or 25
        shape = self.shape.currentData() or "landscape"
        # Sized for the card this will actually run on, which may be
        # smaller than the one the table was measured against.
        width, height, seconds, estimate = self._plan(
            frames, shape, self._settings)
        minutes, rest = divmod(int(estimate), 60)
        said = (f"{seconds:g} seconds of video at {width} x {height}, "
                f"about {minutes} minute{'s' if minutes != 1 else ''} "
                f"{rest} seconds to make.")

        from avcore.video import LENGTHS, SIZES

        sizes = SIZES.get(shape) or SIZES["landscape"]
        measured = dict(zip((count for count, _s, _l, _e in LENGTHS),
                            sizes)).get(frames)
        if measured and (width, height) != measured:
            # Worth saying: somebody who read the release notes will
            # expect the bigger number, and silence would look like a
            # mistake rather than a kindness.
            said += ("  Made smaller to fit this graphics card - asking "
                     "for more than it can hold stops ComfyUI.")
        self.detail.setText(said)

    def choice(self):
        """The frames and shape chosen."""
        return (self.length.currentData() or 25,
                self.shape.currentData() or "landscape")


class ConfirmBulkDelete(QDialog):
    """
    Asks once before deleting a batch.

    Once rather than once per picture: twenty dialogs is not twenty
    times the safety, it is twenty times the clicking, and people stop
    reading by the third. So this one says the number plainly and makes
    keeping them the default.
    """

    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete images")
        self.setModal(True)
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        title = QLabel(
            f"Delete {count} images?" if count != 1
            else "Delete this image?")
        title.setObjectName("dialogTitle")
        outer.addWidget(title)

        body = QLabel(
            "They are removed from disk, not just from the gallery, and "
            "this cannot be undone.\n\n"
            "Anything on the overlay stays there until it is replaced.")
        body.setWordWrap(True)
        body.setObjectName("fieldLabel")
        outer.addWidget(body)

        row = QHBoxLayout()
        row.addStretch(1)
        deny = QPushButton("Keep them" if count != 1 else "Keep it")
        deny.setObjectName("denyButton")
        deny.clicked.connect(self.reject)
        row.addWidget(deny)
        go = QPushButton(f"Delete {count}" if count != 1 else "Delete")
        go.setObjectName("confirmButton")
        go.clicked.connect(self.accept)
        row.addWidget(go)
        outer.addLayout(row)
        deny.setDefault(True)
        deny.setFocus()
