"""
Turning a still image into a short clip.

Kept apart from the image backend rather than folded into it. The two
share only a URL: video needs its own checkpoint, its own node graph and
its own idea of what "done" looks like, and the image path is the one
that has to stay quick and dependable.

Stable Video Diffusion is the model because it fits. It takes an image
and nothing else - no prompt - which is exactly the shape of "animate
this one", and it is the only family that runs in 12GB without
quantised weights and offloading. It produces no sound; nothing that
fits this hardware does.
"""

import json
import time
import urllib.parse
from pathlib import Path

import requests

from .images import GenerationError, _was_interrupted, _why

# Sizes SVD was trained at. Anything else drifts badly, so the source
# image is fitted to one of these rather than used as it comes.
SHAPES = [(1024, 576), (576, 1024), (768, 768)]

# What a clip costs, measured on a 12GB card rather than reasoned about.
#
# A longer clip only fits if each frame is smaller: 50 frames at the full
# 1024x576 does not just run slowly, it takes ComfyUI down - the per-step
# time went 6s, 18s, 34s, 76s as it spilled out of memory, and then the
# process aborted. Trading size for length keeps the total work roughly
# constant, which is why every option below lands near three minutes.
#
#   frames  landscape     seconds  measured
#      25   1024 x 576      2.5      192s
#      50    768 x 432      5.0      199s
#      75    640 x 360      7.5      206s
#     100    512 x 288     10.0      172s
LENGTHS = [
    (25, 2.5, "2.5 seconds", 195),
    (50, 5.0, "5 seconds", 200),
    (75, 7.5, "7.5 seconds", 205),
    (100, 10.0, "10 seconds", 175),
]

# The three shapes, each at the four sizes above. Portrait and square are
# the same pixel budgets as the landscape row they sit beside.
SIZES = {
    "landscape": [(1024, 576), (768, 432), (640, 360), (512, 288)],
    "portrait": [(576, 1024), (432, 768), (360, 640), (288, 512)],
    "square": [(768, 768), (576, 576), (480, 480), (384, 384)],
}

SHAPE_NAMES = [
    ("landscape", "Landscape"),
    ("portrait", "Portrait"),
    ("square", "Square"),
]


# How much work a card can hold, as frames multiplied by pixels.
#
# Pinned by every measurement taken on an 11.94 GiB card, not two of
# them. Four configurations finished:
#
#     25 frames at 1024x576   14.7M     50 at 768x432   16.6M
#     75 frames at  640x360   17.3M    100 at 512x288   14.7M
#
# and one took the GPU down with an illegal memory access, killing
# ComfyUI with it:
#
#     50 frames at 1024x576   29.5M
#
# So the budget must clear 17.3M on 11.94 GiB - 1.45M per GiB - or a
# configuration that demonstrably works would be refused, and it must
# stay well below 2.47M per GiB or the one that crashes gets offered.
# 1.5M keeps all four and sits at 60% of the crash threshold.
#
# An earlier 1.25M looked safe and quietly downgraded the 5s option
# below what this very machine had been measured doing, which is how a
# margin turns into a bug of its own.
BUDGET_PER_GIB = 1_500_000

# Sizes to fall back through when a card cannot hold the measured one.
# Each keeps the shape and steps down by about a third of the pixels.
LADDERS = {
    "landscape": [(1024, 576), (896, 504), (768, 432), (640, 360),
                  (512, 288), (448, 256), (384, 216)],
    "portrait": [(576, 1024), (504, 896), (432, 768), (360, 640),
                 (288, 512), (256, 448), (216, 384)],
    "square": [(768, 768), (672, 672), (576, 576), (480, 480),
               (384, 384), (320, 320), (288, 288)],
}


def card_vram(settings=None):
    """
    How much video memory the card has, in GiB, or None.

    Asked of ComfyUI rather than guessed: it is already running the
    model and already knows. None means it could not be asked, and
    callers treat that as "assume the machine this was measured on"
    rather than refusing to work.
    """
    import json
    import urllib.request

    url = ((settings.get("comfyui.url") if settings else None)
           or "http://127.0.0.1:8188").rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/system_stats", timeout=8) as r:
            stats = json.loads(r.read())
    except Exception:
        return None

    best = 0
    for device in stats.get("devices") or []:
        total = device.get("vram_total") or 0
        if isinstance(total, int):
            best = max(best, total)
    return best / 1_073_741_824 if best else None


def shape_for(width, height):
    """Which of the three shapes a picture is closest to."""
    if not width or not height:
        return "landscape"
    ratio = width / height
    if ratio > 1.2:
        return "landscape"
    if ratio < 0.85:
        return "portrait"
    return "square"


def plan(frames, shape, settings=None):
    """
    The size and the estimate for one choice, for this card.

    Returns (width, height, seconds_of_video, estimated_seconds).

    The measured size is the ceiling, never the floor. A card smaller
    than the one these numbers came from gets a smaller frame rather
    than a crash: asking for more than it can hold does not fail
    politely, it takes the GPU down and ComfyUI with it.
    """
    sizes = SIZES.get(shape) or SIZES["landscape"]
    width = height = None
    seconds = frames / 10
    estimate = LENGTHS[0][3]
    for index, (count, secs, _label, est) in enumerate(LENGTHS):
        if count == frames:
            width, height = sizes[min(index, len(sizes) - 1)]
            seconds, estimate = secs, est
            break
    if width is None:
        width, height = sizes[0]

    vram = card_vram(settings)
    if vram is None:
        return width, height, seconds, estimate

    budget = vram * BUDGET_PER_GIB
    if frames * width * height <= budget:
        return width, height, seconds, estimate

    ladder = LADDERS.get(shape) or LADDERS["landscape"]
    for candidate_w, candidate_h in ladder:
        if candidate_w > width:
            continue          # never larger than what was measured
        if frames * candidate_w * candidate_h <= budget:
            return candidate_w, candidate_h, seconds, estimate

    # Nothing on the ladder fits, so the smallest is offered with the
    # truth about it left to the caller to report.
    smallest = ladder[-1]
    return smallest[0], smallest[1], seconds, estimate


def fits(frames, shape, settings=None):
    """Whether this length can be made at all on this card."""
    vram = card_vram(settings)
    if vram is None:
        return True
    width, height, _s, _e = plan(frames, shape, settings)
    return frames * width * height <= vram * BUDGET_PER_GIB


def best_shape(width, height):
    """
    The trained size closest in shape to the picture given.

    Matching the aspect ratio matters more than matching the size: a
    portrait image squeezed into a landscape frame comes out stretched,
    and SVD has no way to tell you it is unhappy.
    """
    if not width or not height:
        return SHAPES[0]
    ratio = width / height
    return min(SHAPES, key=lambda wh: abs((wh[0] / wh[1]) - ratio))


def fit_to_shape(source, target, dest):
    """
    Crop and scale a still to one of the trained shapes.

    Cropped from the middle rather than squashed. A 1152x896 picture
    fitted to 768x768 by scaling alone comes out visibly narrowed, and
    the first thing anyone notices in the clip is that everything looks
    slightly wrong without being able to say why.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    picture = QImage(str(source))
    if picture.isNull():
        return None

    wide, high = target
    scaled = picture.scaled(wide, high, Qt.KeepAspectRatioByExpanding,
                            Qt.SmoothTransformation)
    left = max(0, (scaled.width() - wide) // 2)
    top = max(0, (scaled.height() - high) // 2)
    cropped = scaled.copy(left, top, wide, high)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(str(dest), "PNG")
    return dest


def poster_path(clip):
    """
    Where a clip's still lives.

    Written by ComfyUI at the same time as the clip. Nothing decodes
    video at runtime: the packaged build leaves PyAV out on purpose, and
    a gallery that shows thumbnails only when run from source would be a
    trap.
    """
    clip = Path(clip)
    return clip.with_name(f"_poster_{clip.stem}.png")


def clip_length(clip, entry=None):
    """
    How long a clip runs, from what was recorded when it was made.

    Taken from the catalogue rather than the file, for the same reason
    as the poster: reading it back would mean decoding video.
    """
    if entry:
        seconds = entry.get("seconds")
        if seconds:
            return float(seconds)
    return None


class VideoBackend:
    """Image to video, through the ComfyUI already being used."""

    extension = "webm"

    def __init__(self, settings):
        self.s = settings
        self.url = (settings.get("comfyui.url")
                    or "http://127.0.0.1:8188").rstrip("/")

    # ---- what is installed ---------------------------------------------

    def checkpoint(self):
        """
        The video checkpoint to use.

        Named explicitly rather than guessed: the folder holds image
        checkpoints too, and feeding one of those to an image-to-video
        graph fails in a way that reads like a bug in this app.
        """
        return (self.s.get("video.checkpoint") or "svd_xt.safetensors").strip()

    def available(self):
        """Is the chosen video model actually there?"""
        from .models import installed
        from .setup import checkpoints_dir

        wanted = self.checkpoint()
        return any(path.name == wanted
                   for path in installed(checkpoints_dir(self.s)))

    # ---- the graph -------------------------------------------------------

    def _workflow(self, image_name, width, height, frames, fps, seed):
        motion = int(self.s.get("video.motion", 127))
        steps = int(self.s.get("video.steps", 20))
        cfg = float(self.s.get("video.cfg", 2.5))
        return {
            "1": {"class_type": "ImageOnlyCheckpointLoader",
                  "inputs": {"ckpt_name": self.checkpoint()}},
            "2": {"class_type": "LoadImage",
                  "inputs": {"image": image_name}},
            "3": {"class_type": "SVD_img2vid_Conditioning",
                  "inputs": {"width": width, "height": height,
                             "video_frames": frames,
                             "motion_bucket_id": motion,
                             "fps": fps,
                             "augmentation_level": 0.0,
                             "clip_vision": ["1", 1],
                             "init_image": ["2", 0],
                             "vae": ["1", 2]}},
            # SVD wants guidance that falls across the clip: a fixed
            # value makes the last frames drift or freeze.
            "4": {"class_type": "VideoLinearCFGGuidance",
                  "inputs": {"min_cfg": 1.0, "model": ["1", 0]}},
            "5": {"class_type": "KSampler",
                  "inputs": {"seed": seed, "steps": steps, "cfg": cfg,
                             "sampler_name": "euler",
                             "scheduler": "karras",
                             "denoise": 1.0,
                             "model": ["4", 0],
                             "positive": ["3", 0],
                             "negative": ["3", 1],
                             "latent_image": ["3", 2]}},
            "6": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveWEBM",
                  "inputs": {"images": ["6", 0],
                             "filename_prefix": "WispWaspClip",
                             "codec": "vp9",
                             "fps": float(fps),
                             "crf": float(self.s.get("video.crf", 32))}},
            # The first frame, saved as a picture for the gallery to
            # draw. Done here rather than by decoding the clip
            # afterwards: the build deliberately leaves PyAV out to save
            # sixty megabytes, and a feature that only works from source
            # is worse than no feature.
            "8": {"class_type": "ImageFromBatch",
                  "inputs": {"image": ["6", 0], "batch_index": 0,
                             "length": 1}},
            "9": {"class_type": "SaveImage",
                  "inputs": {"images": ["8", 0],
                             "filename_prefix": "WispWaspPoster"}},
        }


    # ---- running it ------------------------------------------------------

    def _upload(self, source):
        """
        Put the still where ComfyUI can load it.

        ComfyUI reads inputs from its own folder, and the gallery lives
        somewhere else entirely, so the picture is handed over through
        the upload endpoint rather than by path.
        """
        source = Path(source)
        try:
            with source.open("rb") as handle:
                response = requests.post(
                    f"{self.url}/upload/image",
                    files={"image": (source.name, handle, "image/png")},
                    data={"overwrite": "true"},
                    timeout=60)
            response.raise_for_status()
            return response.json().get("name") or source.name
        except Exception as exc:
            # The raw connection error names a port and a WinError,
            # which tells somebody nothing about what to do next.
            if "refused" in str(exc).lower() or "10061" in str(exc):
                raise GenerationError(
                    "ComfyUI is not running. It stops if a clip asks "
                    "for more than the graphics card can hold - start "
                    "it again from Setup, and try a shorter clip."
                ) from exc
            raise GenerationError(
                f"Could not hand the image to ComfyUI: {exc}") from exc

    def animate(self, source, dest, seed=None, cancel=None,
                on_progress=None):
        """
        Make a clip from one still. Returns the path written.

        `on_progress` is called with a fraction, because this takes
        minutes rather than seconds and a bar that does not move is
        indistinguishable from a hang.
        """
        from PySide6.QtGui import QImage

        source = Path(source)
        if not source.exists():
            raise GenerationError(f"{source.name} is no longer there.")
        if not self.available():
            raise GenerationError(
                f"The video model ({self.checkpoint()}) is not installed. "
                f"Setup can fetch it.")

        picture = QImage(str(source))
        frames = int(self.s.get("video.frames", 25))
        shape = (self.s.get("video.shape")
                 or shape_for(picture.width(), picture.height()))
        width, height, _seconds, _estimate = plan(frames, shape)
        forced = getattr(self, "_forced_shape", None)
        if forced:
            width, height = forced
        # Cropped to the trained shape first, so what SVD sees is the
        # middle of the picture rather than a squashed whole.
        import tempfile

        holding = Path(tempfile.gettempdir()) / f"wispwasp_{source.stem}.png"
        fitted = fit_to_shape(source, (width, height), holding)
        if fitted is not None:
            source = fitted
        fps = int(self.s.get("video.fps", 10))
        seed = seed if seed is not None else int(time.time() * 1000) % 2**31

        name = self._upload(source)
        graph = self._workflow(name, width, height, frames, fps, seed)

        try:
            response = requests.post(f"{self.url}/prompt",
                                     json={"prompt": graph}, timeout=30)
            response.raise_for_status()
            job = response.json()["prompt_id"]
        except Exception as exc:
            raise GenerationError(f"ComfyUI refused the job: {exc}") from exc

        return self._await_clip(job, dest, cancel, on_progress)

    def _await_clip(self, job, dest, cancel=None, on_progress=None):
        """
        Wait for the clip, reporting how far along it is.

        The timeout is generous and separate from the image one: a
        still is seconds, a clip is minutes, and borrowing the image
        timeout would cancel every job just as it got going.
        """
        limit = int(self.s.get("video.timeout", 1800))
        started = time.time()
        missed = 0

        while time.time() - started < limit:
            if cancel and cancel():
                self.interrupt()
                raise GenerationError("cancelled")

            if on_progress:
                on_progress(self._progress(job))

            try:
                history = requests.get(f"{self.url}/history/{job}",
                                       timeout=30).json()
                missed = 0
            except Exception:
                # ComfyUI can die outright rather than fail a job: asking
                # for more frames than the card can hold aborts the whole
                # process. Waiting out a thirty minute timeout after that
                # is indistinguishable from the app hanging, so a run of
                # refused connections is treated as what it is.
                missed += 1
                if missed >= 10:
                    raise GenerationError(
                        "ComfyUI stopped responding while making the "
                        "clip. It usually means the job needed more "
                        "video memory than the card has - try a shorter "
                        "clip, which is also smaller.")
                time.sleep(1.0)
                continue

            entry = history.get(job)
            if not entry:
                time.sleep(1.0)
                continue

            status = entry.get("status", {})
            if status.get("status_str") == "error":
                messages = status.get("messages", [])
                if _was_interrupted(messages):
                    raise GenerationError("cancelled")
                why = _why(messages)
                if "illegal memory access" in why.lower() \
                        or "out of memory" in why.lower():
                    raise GenerationError(
                        "The graphics card ran out of room making this "
                        "clip, which usually stops ComfyUI as well. Try "
                        "a shorter clip - they are made smaller as well "
                        "as shorter, so there is more headroom.")
                raise GenerationError(f"render failed: {why}")

            clip = poster = None
            for key, node in entry.get("outputs", {}).items():
                for item in node.get("images", []) or []:
                    # The still comes from the poster node; anything
                    # else with images is not what is wanted here.
                    if key == "9":
                        poster = item
                for kind in ("videos", "gifs", "images"):
                    for item in node.get(kind, []) or []:
                        if str(item.get("filename", "")).endswith(
                                (".webm", ".mp4")):
                            clip = item
            if clip is not None:
                written = self._fetch(clip, dest)
                if poster is not None:
                    # Beside the clip, named so the gallery's own scan
                    # skips it.
                    self._fetch(poster,
                                written.with_name(
                                    f"_poster_{written.stem}.png"))
                return written
            time.sleep(1.0)

        raise GenerationError(
            f"The clip did not finish within {limit // 60} minutes.")

    def _progress(self, job):
        """
        How far along, as a fraction, or None if it cannot be told.

        Read from the queue rather than guessed from elapsed time: the
        first run of a session loads ten gigabytes off disk before a
        single step happens, and a timer would show that as progress.
        """
        try:
            data = requests.get(f"{self.url}/prompt", timeout=5).json()
            running = data.get("exec_info", {}).get("queue_remaining")
            if running == 0:
                return 1.0
        except Exception:
            pass
        return None

    def _fetch(self, item, dest):
        """Bring the finished file over from ComfyUI's output folder."""
        params = urllib.parse.urlencode({
            "filename": item.get("filename", ""),
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        })
        try:
            response = requests.get(f"{self.url}/view?{params}", timeout=300)
            response.raise_for_status()
        except Exception as exc:
            raise GenerationError(
                f"The clip was made but could not be collected: {exc}"
            ) from exc

        dest = Path(dest)
        suffix = Path(item.get("filename", "")).suffix or ".webm"
        dest = dest.with_suffix(suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return dest

    def interrupt(self):
        try:
            requests.post(f"{self.url}/interrupt", timeout=10)
        except Exception:
            pass
