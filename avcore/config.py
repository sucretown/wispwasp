"""Settings loading and saving, backed by settings.json."""

import copy
import json
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent


def data_dir():
    """
    Where user data lives: settings, images, logs.

    WISPWASP_DATA_DIR is a source-development override used by the test
    harness and tooling that needs an isolated data root. Frozen/installed
    builds deliberately ignore it.

    Source checkouts keep their runtime state together under one ignored
    .wispwasp-data folder. Older source builds wrote directly into the
    repository root, which is how prompt history, generated theme sounds,
    and empty settings files ended up looking like source code.

    Frozen builds keep user data under LocalAppData because the PyInstaller
    unpack directory is temporary and may not be writable.
    """
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        d = base / "WispWasp"
        _migrate_old_data(base / "AudioVision", d)
    else:
        override = os.environ.get("WISPWASP_DATA_DIR", "").strip()
        if override:
            d = Path(override).expanduser()
        else:
            d = APP_DIR / ".wispwasp-data"
            _migrate_source_data(APP_DIR, d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _migrate_source_data(root, new):
    """Move data written by older source builds out of the Git checkout."""
    new.mkdir(parents=True, exist_ok=True)
    for name in (
        "settings.json",
        "prompts.json",
        "favourite_prompts.json",
        "styles.json",
        "output",
        "scratch",
        "sounds",
    ):
        src = root / name
        dst = new / name
        try:
            if src.exists() and not dst.exists():
                src.replace(dst)
        except OSError:
            # Data migration must never prevent the application starting.
            # If a file is locked or Git still owns it, the old path remains
            # untouched and the new data folder is used from this run on.
            pass


def _migrate_old_data(old, new):
    """
    Carry data over from the previous name.

    The app was called AudioVision before, and anyone who ran that build
    has settings and generated images under the old folder. Renaming the
    product should not silently strand them, so the old folder is moved
    across once - and only when there is nothing at the new location to
    overwrite.
    """
    try:
        if old.exists() and not new.exists():
            old.rename(new)
    except OSError:
        # Not worth failing to start over; a fresh folder is made instead.
        pass


DATA_DIR = data_dir()
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULTS = {
    "audio": {
        "device_name": "",        # blank = current Windows default
        "device_kind": "output",  # output = listen in on playback,
                                  # input = a microphone or line in,
                                  # process = one application only
        "process_exe": "",        # e.g. Discord.exe, re-found each run
        "process_name": "",       # the friendly label, for messages
        "mode": "cycle",          # cycle = fixed clips on a timer,
                                  # activation = record while sound lasts
        "record_seconds": 10,
        "cycle_seconds": 22,
        "silence_rms": 250,
        # Sound-activated settings, used only when mode is "activation".
        "activation_rms": 450,    # level that starts a clip
        "activation_silence": 2.0,   # quiet needed to end one
        "activation_max": 30,     # hard limit on a single clip
        "activation_min": 1.2,    # shorter than this is treated as a blip
        "activation_gap": 1.0,    # pause before listening again
    },
    "speech": {
        "model": "small.en",      # tiny.en base.en small.en medium.en
        "cpu_threads": 4,
        "beam_size": 5,
        "min_words": 3,
        "max_prompt_chars": 400,
    },
    "image": {
        "backend": "comfyui",     # comfyui | pollinations
        # Remove the background from generated images, so they sit on
        # the stream as a subject rather than a rectangle. Needs the
        # matting model, which Setup fetches.
        "cutout": False,
        "width": 1344,
        "height": 768,
        "style_suffix": "highly detailed, dramatic lighting",
        "style_name": "Detailed",   # which preset that suffix came from
        "negative_prompt": "text, watermark, signature, blurry, deformed",
        "amount": 1,
        "keep_images": 40,
        "manual_auto_push": True,  # manual images go straight to the overlay
    },
    "safety": {
        # One tick, five layers: safety terms on the negative prompt,
        # blocked words refused before generating, adult models hidden,
        # every picture scored, and anything flagged kept off the
        # overlay. Off by default, so nothing changes for anybody who
        # does not ask for it.
        "safe_mode": False,
    },
    "comfyui": {
        "url": "http://127.0.0.1:8188",
        "path": "",               # blank = %USERPROFILE%\\ComfyUI
        "checkpoint": "",         # blank = first one found
        # LoRAs applied on top of the checkpoint, in this order: each
        # entry is {"name": file, "strength": float, "on": bool}. Order
        # matters, because each is applied over the last.
        "loras": [],
        "steps": 20,
        "cfg": 6.5,
        "sampler": "dpmpp_2m",
        "scheduler": "karras",
        "timeout": 180,
        "autostart": True,
    },
    "paths": {
        "overlay_dir": "output",  # relative to the data folder
        "manual_dir": "",         # blank = Desktop
    },
    "server": {
        "port": 8420,
    },
    "ui": {
        "theme": "dark",
        "accent": "#6ea8fe",
        "layout": "hybrid",       # hybrid | sidebar | split
        "warn_on_explicit_suffix": True,
        "autostart_listening": False,
        "favourites_open": True,   # the Favourite prompts section
        "live_split": None,        # image / history divider position
        "confirm_settings": True,   # hold changes until Apply
        "gallery_page_size": 24,    # thumbnails drawn at once
        "theme_main": "",           # blank = the built-in colour
        "theme_accent": "",
        "decor_theme": "none",     # decorative theme, none by default
        "decor_animate": True,      # the Fancy toggle
        "decor_sounds": False,      # opt-in, silent unless asked for
        "decor_enhanced": False,    # opt-in, richer artwork
    },
    "models": {
        # Optional. Most checkpoints download without one; a few
        # are gated behind a Civitai account, and this is how an
        # advanced user reaches those.
        "civitai_key": "",
        # Which model setup fetches. Blank means the default,
        # so an existing install keeps behaving as it did.
        "tier": "",
    },
    "hotkeys": {
        # Single keys, acted on while the Live or Prompt page is showing
        # and nothing is being typed into. Blank disables one.
        "listen": "Space",
        "capture": "V",
        "repeat": "R",
        "cancel": "C",
        "clear": "X",
    },
}


def _merge(base, over):
    """Recursive merge, so new default keys appear in older settings files."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Dict-backed settings with dotted access: s.get('image.width')."""

    def __init__(self, data=None, path=None):
        self.path = Path(path) if path else SETTINGS_PATH
        self.data = _merge(DEFAULTS, data or {})

    @classmethod
    def load(cls, path=None):
        p = Path(path) if path else SETTINGS_PATH
        raw = {}
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[config] {p.name} unreadable ({exc}); using defaults")
                raw = {}
        return cls(raw, p)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)     # atomic, so a crash can't truncate settings

    def dir_for(self, dotted, fallback):
        """
        Resolve a folder setting to an absolute path, creating it.

        Relative values are taken against the data folder rather than the
        working directory. In a frozen build the working directory is
        wherever the shortcut happened to point, so a bare "output" would
        otherwise scatter images somewhere unpredictable.
        """
        raw = (self.get(dotted) or "").strip()
        if not raw:
            raw = fallback
        p = Path(raw)
        if not p.is_absolute():
            p = DATA_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted, value):
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def reset(self):
        self.data = copy.deepcopy(DEFAULTS)
