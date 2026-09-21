"""
Safe mode: one tick, five layers.

None of them is reliable alone, and saying so plainly matters more than
the feature does. A word list misses euphemism. A negative prompt is a
suggestion, not a rule. A classifier has a false-negative rate nobody
publishes honestly. What the layers give together is "unlikely, and
unlikely to surprise you on stream" - not "impossible".

The one deterministic layer is the model: a checkpoint trained to
refuse is worth more than everything else here combined, and a
checkpoint merged for explicit output will defeat all of it.
"""

import re
from pathlib import Path

# Added to the negative prompt while safe mode is on. Kept short: a
# negative prompt that runs to fifty terms starts steering the picture
# in ways nobody asked for.
NEGATIVE_TERMS = (
    "nsfw, nudity, nude, explicit, sexual, gore, blood, "
    "suggestive, underwear, lingerie"
)

# Words that stop a prompt before it is generated. Deliberately short
# and unambiguous: every addition trades a miss for a false positive,
# and refusing somebody's innocent prompt is its own kind of failure.
BLOCKED = (
    "nsfw", "nude", "nudes", "naked", "porn", "porno", "pornographic",
    "explicit", "hentai", "erotic", "erotica", "sex", "sexual", "xxx",
    "topless", "bottomless", "genitals", "genitalia", "masturbat",
    "orgasm", "fellatio", "cunnilingus", "bdsm", "fetish",
)

_WORD = re.compile(r"[a-z]+")


def blocked_terms(text):
    """
    Which blocked words a prompt contains.

    Matched on whole words, with a prefix allowance for the few entries
    that end mid-word, so "sex" does not fire on "Essex" and "sussex".
    """
    if not text:
        return []
    words = set(_WORD.findall(text.lower()))
    found = []
    for term in BLOCKED:
        if term.endswith("-") or term in ("masturbat",):
            if any(word.startswith(term) for word in words):
                found.append(term)
        elif term in words:
            found.append(term)
    return found


def prompt_is_blocked(text):
    return bool(blocked_terms(text))


def negative_with_safety(existing):
    """The user's negative prompt, with the safety terms added once."""
    existing = (existing or "").strip().rstrip(",")
    if not existing:
        return NEGATIVE_TERMS
    if NEGATIVE_TERMS in existing:
        return existing
    return f"{existing}, {NEGATIVE_TERMS}"


def is_on(settings):
    """Is safe mode switched on?"""
    try:
        return bool(settings.get("safety.safe_mode", False))
    except Exception:
        return False


# ---- the classifier ----------------------------------------------------
# Bundled as assets/image-safety-xs.onnx from:
# https://huggingface.co/OwenElliott/image-safety-classifier-xs
# The model repository declares MIT licensing. Keep THIRD_PARTY_NOTICES.md
# in sync if this asset is replaced.

CLASSES = ("NSFL", "NSFW", "SFW")

# Anything below `SAFE` in the safe class is not waved through, and
# anything above `UNSAFE` in the unsafe classes is held back. Between
# them is "unsure": blurred, but not treated as proof of anything.
SAFE = 0.80
UNSAFE = 0.50

_session = None
_failed = False


def _model_path():
    from avgui.assets import asset

    return Path(asset("image-safety-xs.onnx"))


def _load():
    """
    The ONNX session, built once.

    Bundled rather than fetched, so ticking the box works immediately.
    It is 13MB, which is small enough that everybody carrying it costs
    less than anybody waiting for it.
    """
    global _session, _failed
    if _session is not None or _failed:
        return _session
    try:
        import onnxruntime

        path = _model_path()
        if not path.exists():
            _failed = True
            return None
        _session = onnxruntime.InferenceSession(
            str(path), providers=["CPUExecutionProvider"])
    except Exception:
        _failed = True
        _session = None
    return _session


def scores(path):
    """
    What the classifier makes of one picture, or None.

    None means it could not look - a missing model, an unreadable file.
    Callers treat that as "unsure" rather than as "safe": a safety check
    that fails open is worse than none, because it is trusted.
    """
    session = _load()
    if session is None:
        return None

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        import numpy

        picture = QImage(str(path))
        if picture.isNull():
            return None
        small = picture.convertToFormat(QImage.Format_RGB888).scaled(
            224, 224, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        raw = numpy.frombuffer(small.constBits().tobytes(), dtype="uint8")
        # Qt pads each row to a four-byte boundary, so the buffer is
        # wider than the picture and has to be trimmed rather than
        # reshaped blindly.
        raw = raw.reshape(224, small.bytesPerLine() // 3, 3)[:, :224, :]
        array = raw.astype("float32").transpose(2, 0, 1)[None]
        name = session.get_inputs()[0].name
        result = session.run(None, {name: array})[0][0]
        return {label: float(value) for label, value in zip(CLASSES, result)}
    except Exception:
        return None


def verdict(path):
    """
    "safe", "unsure" or "unsafe" for one picture.

    Unsure is a real answer, not a rounding of one of the others. It is
    what the middle of the range deserves, and treating it as safe is
    how a filter earns its reputation.
    """
    found = scores(path)
    if not found:
        return "unsure"
    if found.get("SFW", 0.0) >= SAFE:
        return "safe"
    if (found.get("NSFW", 0.0) + found.get("NSFL", 0.0)) >= UNSAFE:
        return "unsafe"
    return "unsure"
