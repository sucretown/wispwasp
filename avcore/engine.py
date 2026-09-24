"""
The engine: owns all mutable state, the worker thread, and the manual queue.

Panels never hold state of their own. They subscribe with add_listener() and
redraw from the snapshot they get, which is what lets the window swap between
layouts mid-session without anything being lost.

One worker thread does every GPU job. Manual prompts are drained before the
next listen cycle, so firing one pauses listening as a consequence of the
structure rather than by way of a lock.
"""

import json
import os
import queue
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .audio import Recorder
from .config import Settings
from .images import GenerationError, make_backend
from .speech import Transcriber, build_prompt, is_junk


def _desktop():
    """
    The real Desktop path.

    Read from the registry rather than assumed to be ~/Desktop, because
    OneDrive redirects it and the assumed path then does not exist.
    """
    try:
        import winreg
        key = (r"Software\Microsoft\Windows\CurrentVersion"
               r"\Explorer\Shell Folders")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _ = winreg.QueryValueEx(k, "Desktop")
            p = Path(value)
            if p.exists():
                return p
    except Exception:
        pass
    return Path.home() / "Desktop"


class ManualJob:
    """A user-typed prompt waiting to render."""

    def __init__(self, prompt, amount=1, auto_push=None, save_dir=None,
                 base=None, suffix=""):
        self.prompt = prompt
        self.amount = max(1, min(50, int(amount)))
        self.auto_push = auto_push
        self.save_dir = save_dir
        # The words before any style was appended, and the style itself.
        # Kept apart so the history can show them differently and copy
        # either one - merged into a single string they cannot be
        # separated again, since a style is just more words.
        self.base = base if base is not None else prompt
        self.suffix = suffix or ""


class Engine:
    def __init__(self, settings=None):
        self.s = settings or Settings.load()
        self._listeners = []
        self._level_listeners = []
        self._lock = threading.RLock()

        self._thread = None
        self._shutdown = threading.Event()
        self._listening = threading.Event()
        self._manual = queue.Queue()
        self._cancel = threading.Event()
        self._capture_now = threading.Event()
        self._reload_audio = threading.Event()

        self._transcriber = None
        self._recorder = None
        self._backend = None
        # Which kind the current backend object was built for, so a
        # change of mind can be noticed.
        self._backend_kind = None
        # Set when a clip should stop; read by the video backend's own
        # polling loop.
        self._video_cancel = False
        self._launcher = None
        self._comfy_attempt = 0.0
        self._clip_seq = 0
        self._history = deque(maxlen=60)
        self._typed = deque(maxlen=40)
        self._last_prompt = None
        from .catalog import Catalog, PromptStore
        from .config import DATA_DIR
        # Records which prompt made which image, so the gallery can still
        # answer that question in a later session.
        self.catalog = Catalog(DATA_DIR / "prompts.json")
        # Prompts the user chose to keep, which outlive any one image.
        self.prompts = PromptStore(DATA_DIR / "favourite_prompts.json")
        # Named suffix styles: the built-in ones plus the user's own.
        from .styles import StylePresets
        self.styles = StylePresets(DATA_DIR / "styles.json")

        self.state = {
            "mode": "idle",        # idle | recording | transcribing
                                   # | generating | loading
            "status": "Not started",
            "listening": False,
            "busy": False,
            "transcript": "",
            "prompt": "",
            "skip_reason": "",
            "image": None,         # path of whatever the overlay shows
            "image_source": "",    # live | manual
            "queued": 0,
            "cycles": 0,
            "generated": 0,
            # Why the rest came to nothing. Counted per reason because
            # "too quiet" is the app working correctly and "render
            # failed" is not, and a single gap between two numbers
            # cannot tell you which you have been getting.
            "skips": {},
            "failures": 0,
            "cancelled": 0,        # stopped on purpose; neither of those
            "error": "",
            "pending_manual": None,  # last manual image awaiting a push
            "next_in": None,         # seconds until the next capture
            "device": "",            # which output is being listened to
            "history": [],           # recent transcripts, newest first
            "can_repeat": False,
            "overlay_cleared": False,
            "comfy_ready": False,    # image backend answering yet
            "typed": [],             # prompts typed by hand, newest first
            # Loaded rather than empty: kept prompts are on disk and
            # should be there the moment the window opens.
            "favourites": self.prompts.all(),
            # Style presets, published so every panel showing them draws
            # from one source instead of each keeping its own copy.
            "styles": self.styles.all(),
            "style_name": self.s.get("image.style_name", "") or "",
        }

    def set_style(self, name):
        """
        Choose the style appended to every prompt.

        Writes the suffix as well as the name, because everything that
        builds a prompt reads the suffix and should not have to know that
        presets exist.
        """
        preset = self.styles.find(name)
        if preset is None:
            return False
        self.s.set("image.style_name", preset["name"])
        self.s.set("image.style_suffix", preset["suffix"])
        self.s.save()
        self._set(styles=self.styles.all(), style_name=preset["name"])
        return True

    def refresh_styles(self):
        """
        Republish the list after one was added, edited or removed.

        Also re-reads the suffix, since editing the style in use changes
        what every prompt gets without its name changing.
        """
        current = self.s.get("image.style_name", "") or ""
        preset = self.styles.find(current)
        if preset is None:
            current = ""
            self.s.set("image.style_name", "")
            self.s.save()
        elif preset["suffix"] != self.s.get("image.style_suffix"):
            self.s.set("image.style_suffix", preset["suffix"])
            self.s.save()
        self._set(styles=self.styles.all(), style_name=current)

    def favourite_prompt(self, prompt):
        """Keep a prompt. Returns True if it was not already kept."""
        added = self.prompts.add(prompt)
        if added:
            self._set(favourites=self.prompts.all())
        return added

    def unfavourite_prompt(self, prompt):
        removed = self.prompts.remove(prompt)
        if removed:
            self._set(favourites=self.prompts.all())
        return removed

    def is_favourite_prompt(self, prompt):
        return self.prompts.has(prompt)

    # ---- observers -----------------------------------------------------

    def add_listener(self, fn):
        """fn(snapshot_dict) is called on every state change, off-thread."""
        with self._lock:
            self._listeners.append(fn)
        fn(self.snapshot())

    def remove_listener(self, fn):
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def add_level_listener(self, fn):
        """
        fn(rms) during recording, about 20 times a second.

        Deliberately separate from the state listeners. Pushing the audio
        level through the snapshot path would copy the whole state dict and
        wake every panel twenty times a second to redraw things that have
        not changed.
        """
        with self._lock:
            self._level_listeners.append(fn)

    def remove_level_listener(self, fn):
        with self._lock:
            if fn in self._level_listeners:
                self._level_listeners.remove(fn)

    def _emit_level(self, rms):
        with self._lock:
            watchers = list(self._level_listeners)
        for fn in watchers:
            try:
                fn(rms)
            except Exception:
                pass

    def snapshot(self):
        with self._lock:
            return dict(self.state)

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)
            snap = dict(self.state)
            watchers = list(self._listeners)
        for fn in watchers:
            try:
                fn(snap)
            except Exception:
                pass  # a broken panel must never kill the worker

    # ---- lifecycle -----------------------------------------------------

    def start(self):
        """Spin up the worker. Safe to call twice."""
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(
            target=self._worker, name="av-worker", daemon=True)
        self._thread.start()

    def shutdown(self, wait=6):
        self._shutdown.set()
        self._listening.clear()
        self._cancel.set()
        if self._thread:
            self._thread.join(timeout=wait)
        self._clear_scratch()
        # Only stops a ComfyUI this app started; one the user was already
        # running is left alone.
        if self._launcher:
            self._launcher.stop()

    def start_listening(self):
        self._listening.set()
        self._set(listening=True, status="Listening")

    def stop_listening(self):
        self._listening.clear()
        self._set(listening=False, status="Stopped")

    def toggle_listening(self):
        if self._listening.is_set():
            self.stop_listening()
        else:
            self.start_listening()

    def cancel_current(self):
        """Abandon the in-flight recording or render."""
        self._cancel.set()
        if self._backend:
            self._backend.interrupt()

    def capture_now(self):
        """
        Skip the rest of the wait and record immediately.

        Starts listening if it was stopped, so one button does the obvious
        thing rather than being ignored when idle.
        """
        self._capture_now.set()
        if not self._listening.is_set():
            self.start_listening()
        self._set(status="Capturing now...")

    def repeat_last(self):
        """Render the last prompt again, without retyping it."""
        prompt = self._last_prompt
        if not prompt:
            return False
        return self.queue_manual(prompt)

    def reload_audio(self):
        """
        Pick up a change of audio device.

        The recorder holds an open PyAudio handle, so it cannot simply be
        swapped from another thread. The flag is read by the worker between
        cycles, which is the only safe moment to close and rebuild it.
        """
        self._reload_audio.set()
        if not self._listening.is_set():
            # Nothing is cycling, so apply it now for immediate feedback.
            self._swap_recorder()

    def _build_recorder(self):
        """
        Make a recorder for whatever the settings ask for.

        Three kinds of source: an output to listen in on, a microphone, or
        a single application. They all present the same record() contract,
        so nothing downstream has to know which one it got.
        """
        kind = self.s.get("audio.device_kind", "output") or "output"
        if kind == "process":
            from .process_audio import ProcessRecorder, find_by_name
            exe = self.s.get("audio.process_exe", "")
            target = find_by_name(exe)
            if target is None:
                raise RuntimeError(
                    f"{self.s.get('audio.process_name') or exe} is not "
                    f"running, so there is nothing to capture from it.")
            return ProcessRecorder(target["pid"], target["name"])
        return Recorder(self.s.get("audio.device_name"), kind)

    def _swap_recorder(self):
        self._reload_audio.clear()
        old = self._recorder
        self._recorder = None
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        try:
            self._recorder = self._build_recorder()
            self._set(device=self._recorder.label, error="")
        except Exception as exc:
            self._set(error=f"Audio device: {exc}")

    def _remember(self, text, prompt=None, skipped="", image=None,
                  kind="heard", base=None, suffix=""):
        """
        Add an entry to the transcript history.

        `kind` is what the list colours itself by: heard, typed, or
        skipped. Deriving it later from whether a prompt exists would get
        a skipped manual prompt wrong.

        `base` and `suffix` keep the words and the style apart, so the
        history can dim the style and copy either half on its own.
        """
        entry = {
            "at": datetime.now().strftime("%H:%M:%S"),
            "text": (text or "").strip(),
            "prompt": prompt,
            "base": (base if base is not None else prompt) or "",
            "suffix": suffix or "",
            "skipped": skipped,
            "kind": "skipped" if skipped else kind,
            "image": str(image) if image else None,
        }
        self._history.appendleft(entry)
        if prompt:
            self._last_prompt = prompt
        self._set(history=list(self._history), can_repeat=bool(
            self._last_prompt))
        return entry

    def queue_manual(self, prompt, amount=1, auto_push=None, save_dir=None,
                     base=None, suffix=""):
        """
        Queue a typed prompt. It runs before the next listen cycle, and
        cancels an in-progress recording so the user isn't left waiting.
        """
        prompt = " ".join((prompt or "").split())
        if not prompt:
            return False
        self._manual.put(ManualJob(prompt, amount, auto_push, save_dir,
                                   base=base, suffix=suffix))

        # Kept here rather than in whichever panel happened to send it.
        # The prompt strip and the Prompt panel are two ways into the same
        # queue, and a list owned by one of them shows only half the story.
        if prompt in self._typed:
            self._typed.remove(prompt)
        self._typed.appendleft(prompt)

        self._set(queued=self._manual.qsize(), typed=list(self._typed))
        if self.state["mode"] == "recording":
            self._cancel.set()
        return True

    def clear_manual_queue(self):
        while not self._manual.empty():
            try:
                self._manual.get_nowait()
            except queue.Empty:
                break
        self._set(queued=0)

    # ---- worker --------------------------------------------------------

    def _worker(self):
        # Bring the image backend up before anything asks for a picture.
        # Waiting until the first render meant Settings could not list the
        # installed models until you had already generated something.
        self._warm_up()

        while not self._shutdown.is_set():
            # Manual work always wins. Draining here is what pauses
            # listening: the loop simply can't reach a cycle until it's done.
            try:
                job = self._manual.get_nowait()
            except queue.Empty:
                job = None

            if job is not None:
                self._set(queued=self._manual.qsize())
                self._run_manual(job)
                continue

            if self._listening.is_set():
                self._listen_cycle()
            else:
                self._set(mode="idle", busy=False)
                time.sleep(0.25)

    def _warm_up(self):
        """
        Start ComfyUI at launch rather than at the first render.

        Runs on the worker thread, so the window stays responsive while
        ComfyUI takes its half minute to come up. Anything queued in the
        meantime simply waits, which it would have had to do anyway.

        Nothing here is fatal: if ComfyUI cannot start, the app is still
        perfectly usable for changing settings and running setup, and the
        normal retry before each render still applies.
        """
        try:
            self._sync_backend()
        except Exception as exc:
            self._set(error=str(exc))
            return

        if self.s.get("image.backend", "comfyui") != "comfyui":
            self._set(comfy_ready=True)
            return

        if self._backend.is_up():
            self._set(comfy_ready=True, status="Ready")
            return

        if not self.s.get("comfyui.autostart", True):
            self._set(comfy_ready=False)
            return

        self._set(mode="loading", status="Starting ComfyUI...")
        ready = self._ensure_comfy()
        self._set(mode="idle",
                  comfy_ready=bool(ready),
                  status="Ready" if ready else "ComfyUI is not running")

    def _ensure_comfy(self):
        """
        Make sure ComfyUI is answering, starting it if not.

        Checked before every render rather than once per session. The
        original one-shot version had a hole: if ComfyUI happened to be up
        at startup the engine ticked the box and never looked again, so
        when that instance later went away every render failed forever
        with no way back short of restarting the app.

        Attempts are spaced out because starting ComfyUI takes the better
        part of a minute, and a failing cycle every twenty seconds would
        otherwise pile up processes.
        """
        if self.s.get("image.backend", "comfyui") != "comfyui":
            return True
        if not self.s.get("comfyui.autostart", True):
            return True
        if self._backend is not None and self._backend.is_up():
            if not self.state.get("comfy_ready"):
                self._set(comfy_ready=True)
            return True

        # It was up and is not any more, so anything relying on it - the
        # model list in Settings, for one - needs to know.
        if self.state.get("comfy_ready"):
            self._set(comfy_ready=False)

        from .comfy_launcher import ComfyLauncher
        if self._launcher is None:
            self._launcher = ComfyLauncher(self.s)

        if self._launcher.is_starting():
            # Already booting from a previous cycle; give it this cycle's
            # worth of time rather than spawning a second one.
            self._set(mode="loading", status="Waiting for ComfyUI...")
            return self._launcher.wait_until_up(60,
                                                on_progress=self._status)

        since = time.time() - self._comfy_attempt
        if since < 25:
            return False
        self._comfy_attempt = time.time()

        self._set(mode="loading", status="Starting ComfyUI...")
        if self._launcher.start(on_progress=self._status):
            self._set(error="", comfy_ready=True)
            return True
        self._set(error=self._launcher.error or
                  "ComfyUI could not be started.", comfy_ready=False)
        return False

    def _status(self, message):
        self._set(status=message)

    def _count_skip(self, reason):
        """Record that a listen was heard but deliberately not used."""
        tally = dict(self.state.get("skips") or {})
        key = (reason or "other").strip() or "other"
        tally[key] = tally.get(key, 0) + 1
        return tally

    def _sync_backend(self):
        """
        Make sure the backend matches what is currently chosen.

        It used to be built once and kept for the life of the process,
        so switching between ComfyUI and Pollinations in Settings
        changed nothing until the app was restarted - generations
        carried on going wherever they had been going, which looks
        exactly like the setting being ignored.
        """
        if self._backend is not None and self._backend_kind is None:
            # Put there from outside - the demo stubs, or a test double.
            # Rebuilding it from settings would quietly throw away
            # whatever the caller meant to use.
            return self._backend

        wanted = self.s.get("image.backend", "comfyui") or "comfyui"
        if self._backend is not None and self._backend_kind == wanted:
            return self._backend

        previous = self._backend
        self._backend = make_backend(self.s)
        self._backend_kind = wanted
        if previous is not None:
            # A change of backend means whatever the old one thought
            # about ComfyUI being up no longer applies.
            self._set(comfy_ready=False)
        return self._backend

    def _ensure_ready(self):
        """Load the slow pieces on first use, reporting progress."""
        self._sync_backend()

        self._ensure_comfy()

        if self._recorder is None:
            self._recorder = self._build_recorder()
            self._set(device=self._recorder.label)
        if self._transcriber is None:
            self._set(mode="loading", status="Loading speech model...")
            self._transcriber = Transcriber(
                model=self.s.get("speech.model", "small.en"),
                cpu_threads=int(self.s.get("speech.cpu_threads", 4)),
                beam_size=int(self.s.get("speech.beam_size", 5)),
            )

    def _clip_path(self):
        """
        The scratch file each recording is written to.

        Absolute, because a relative name lands in whatever the working
        directory happens to be - which for an installed copy is a folder
        the app may not be allowed to write to. Named per process, because
        two copies running at once would otherwise fight over one file and
        one of them would get a permission error mid-recording.
        """
        folder = self.s.dir_for("paths.overlay_dir", "output").parent
        scratch = folder / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        suffix = f"_{self._clip_seq}" if self._clip_seq else ""
        return scratch / f"clip_{os.getpid()}{suffix}.wav"

    def _clear_scratch(self):
        """Remove this process's scratch files, and any left by dead ones."""
        try:
            folder = (self.s.dir_for("paths.overlay_dir", "output").parent
                      / "scratch")
        except OSError:
            return
        if not folder.exists():
            return
        mine = f"clip_{os.getpid()}"
        for f in folder.glob("clip_*.wav"):
            # Anything older than a day belonged to a run that is long
            # gone, so it is safe to clear out too.
            try:
                stale = (time.time() - f.stat().st_mtime) > 86400
                if f.name.startswith(mine) or stale:
                    f.unlink()
            except OSError:
                pass

    def _cancelled(self):
        return self._cancel.is_set() or self._shutdown.is_set()

    def _capture_clip(self, clip):
        """
        Record one clip, whichever way the settings ask for.

        Returns (rms, note) where note explains an activation clip that
        was discarded, or "" when the clip is worth transcribing.
        """
        if (self.s.get("audio.mode", "cycle") or "cycle") != "activation":
            secs = int(self.s.get("audio.record_seconds", 10))
            self._set(mode="recording", busy=True, error="", skip_reason="",
                      status=f"Recording {secs}s...")
            rms = self._recorder.record(clip, secs, cancel=self._cancelled,
                                        on_level=self._emit_level)
            return rms, ""

        from .audio import ActivationGate, RECORDING
        gate = ActivationGate(
            threshold=float(self.s.get("audio.activation_rms", 450)),
            silence_seconds=float(self.s.get("audio.activation_silence", 2)),
            max_seconds=float(self.s.get("audio.activation_max", 30)),
            min_seconds=float(self.s.get("audio.activation_min", 1.2)),
        )

        self._set(mode="recording", busy=True, error="", skip_reason="",
                  status="Waiting for sound...")

        def announce(state, g):
            if state == RECORDING:
                self._set(status="Recording while it lasts...")

        rms, gate = self._recorder.record_activated(
            clip, gate, cancel=self._cancelled,
            on_level=self._emit_level, on_state=announce)

        if self._cancelled():
            return rms, "cancelled"
        if gate.state != "done":
            return rms, "nothing was heard"
        if gate.too_short():
            # A door closing or a click clears the threshold briefly.
            return rms, (f"only {gate.speech_seconds:.1f}s of sound, "
                         f"under the {gate.min_seconds:.1f}s minimum")
        self._set(status=f"Heard {gate.speech_seconds:.1f}s "
                         f"({gate.reason})")
        return rms, ""

    def _listen_cycle(self):
        self._cancel.clear()
        self._capture_now.clear()
        try:
            self._ensure_ready()
        except Exception as exc:
            self._set(mode="idle", busy=False, error=str(exc),
                      status="Setup failed")
            self._listening.clear()
            self._set(listening=False)
            return

        # Between cycles is the only safe moment to rebuild the recorder,
        # since it owns an open audio handle.
        if self._reload_audio.is_set():
            self._swap_recorder()

        clip = self._clip_path()
        try:
            rms, note = self._capture_clip(clip)
        except PermissionError:
            # The scratch file is per-process, so this normally means
            # antivirus or a sync client is holding it. Recording carries
            # on under a fresh name rather than stopping.
            self._clip_seq += 1
            self._set(mode="idle", busy=False,
                      error=f"Could not write {clip.name}; trying another "
                            f"name. If this keeps happening, check whether "
                            f"antivirus or a sync tool is watching "
                            f"{clip.parent}.")
            time.sleep(1)
            return
        except Exception as exc:
            self._set(mode="idle", busy=False, error=f"Audio: {exc}")
            time.sleep(1)
            return

        if self._cancelled():
            self._set(mode="idle", busy=False, status="Cycle cancelled")
            return

        if note:
            # Sound-activated capture that did not produce anything worth
            # transcribing, and says which of the reasons applied.
            self._set(mode="idle", busy=False, skip_reason=note,
                      status=f"Skipped - {note}",
                      cycles=self.state["cycles"] + 1,
                      skips=self._count_skip(note))
            self._remember("", skipped=note)
            self._wait_cycle()
            return

        if rms is not None and rms < int(self.s.get("audio.silence_rms", 250)):
            self._set(mode="idle", busy=False, skip_reason="silence",
                      status="Silence - skipped",
                      cycles=self.state["cycles"] + 1,
                      skips=self._count_skip("too quiet"))
            self._remember("", skipped="silence")
            self._wait_cycle()
            return

        self._set(mode="transcribing", status="Transcribing...")
        try:
            text = self._transcriber.transcribe(clip)
        except Exception as exc:
            self._set(mode="idle", busy=False, error=f"Speech: {exc}")
            time.sleep(1)
            return

        self._set(transcript=text)
        junk, reason = is_junk(text, int(self.s.get("speech.min_words", 3)))
        if junk:
            self._set(mode="idle", busy=False, skip_reason=reason,
                      status=f"Skipped - {reason}",
                      cycles=self.state["cycles"] + 1,
                      skips=self._count_skip(reason))
            self._remember(text, skipped=reason)
            self._wait_cycle()
            return

        style = self.s.get("image.style_suffix", "")
        prompt = build_prompt(
            text,
            style_suffix=style,
            max_chars=int(self.s.get("speech.max_prompt_chars", 400)),
            min_words=int(self.s.get("speech.min_words", 3)),
        )
        # The same words without the style, so the history can show and
        # copy them separately.
        bare = build_prompt(
            text,
            style_suffix="",
            max_chars=int(self.s.get("speech.max_prompt_chars", 400)),
            min_words=int(self.s.get("speech.min_words", 3)),
        )
        self._set(prompt=prompt, cycles=self.state["cycles"] + 1)

        # Refused before anything is generated, while safe mode is on.
        # Counted as a skip like any other, so the tally on the Live
        # page still adds up.
        from .safety import is_on as _safe_mode
        from .safety import prompt_is_blocked

        if _safe_mode(self.s) and prompt_is_blocked(prompt):
            self._set(mode="idle", busy=False, skip_reason="safe mode",
                      status="Skipped - safe mode",
                      skips=self._count_skip("safe mode"))
            self._remember(text, skipped="safe mode")
            self._wait_cycle()
            return

        made = self._render(prompt, source="live")
        if made is not None:
            if self._allowed_on_overlay(made):
                self.publish_overlay(made, source="live")
            self._set(status="Listening")
        self._remember(text, prompt=prompt, image=made,
                       base=bare or prompt, suffix=style)
        self._wait_cycle()

    def _wait_cycle(self):
        """
        Sleep out the rest of the cycle, but wake instantly for a manual
        prompt, a capture request or a stop. Polling beats a long sleep for
        responsiveness.
        """
        self._set(mode="idle", busy=False)
        if (self.s.get("audio.mode", "cycle") or "cycle") == "activation":
            # There is no cycle to wait out: the next clip starts as soon
            # as something is heard. A short pause only stops one long
            # utterance being chopped into two by the trailing silence.
            remaining = float(self.s.get("audio.activation_gap", 1.0))
            end = time.time() + remaining
            while time.time() < end:
                if (self._shutdown.is_set() or not self._listening.is_set()
                        or not self._manual.empty()
                        or self._capture_now.is_set()):
                    break
                time.sleep(0.1)
            return

        target = int(self.s.get("audio.cycle_seconds", 22))
        recorded = int(self.s.get("audio.record_seconds", 10))
        remaining = max(0, target - recorded)
        end = time.time() + remaining
        shown = None
        while time.time() < end:
            if (self._shutdown.is_set() or not self._listening.is_set()
                    or not self._manual.empty()
                    or self._capture_now.is_set()):
                break
            # Only publish when the whole second changes, so the countdown
            # costs one update a second rather than five.
            left = int(round(end - time.time()))
            if left != shown:
                shown = left
                self._set(next_in=left)
            time.sleep(0.2)
        self._set(next_in=None)

    def _render(self, prompt, source, dest=None):
        """Generate one image and hand it to the overlay."""
        self._cancel.clear()

        # Confirm the backend is actually there before spending a cycle on
        # a request that cannot land. Waiting here is better than a failed
        # render, since ComfyUI may simply still be booting.
        if not self._ensure_comfy():
            self._set(mode="idle", busy=False,
                      status="Waiting for ComfyUI",
                      error=self.state.get("error") or
                      "ComfyUI is not running yet, so nothing can be "
                      "generated. It should be ready shortly.")
            return None

        self._set(mode="generating", busy=True, status="Generating...")
        ext = self._backend.extension
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        out_dir = self.s.dir_for("paths.overlay_dir", "output")
        target = dest or out_dir / f"{source}_{stamp}.{ext}"

        try:
            self._backend.generate(prompt, target, cancel=self._cancelled)
        except GenerationError as exc:
            msg = str(exc)
            if msg == "cancelled":
                # Neither a skip nor a failure: the person stopped it on
                # purpose, so it is taken back out of the count rather
                # than left sitting in the gap between listens and
                # images looking like something went wrong. Only live
                # listens are counted, so a cancelled manual render has
                # nothing to take back.
                self._set(mode="idle", busy=False, status="Cancelled",
                          cancelled=self.state.get("cancelled", 0) + 1,
                          cycles=max(0, self.state["cycles"] - 1)
                          if source == "live" else self.state["cycles"])
                return None
            self._set(mode="idle", busy=False, error=msg,
                      status="Generation failed",
                      failures=self.state.get("failures", 0) + 1)
            return None
        except Exception as exc:
            self._set(mode="idle", busy=False, error=str(exc),
                      status="Generation failed",
                      failures=self.state.get("failures", 0) + 1)
            return None

        self._set(generated=self.state["generated"] + 1)
        # Recorded here rather than at the call sites, so live cycles,
        # manual prompts and batches are all covered by one line.
        backend = self._backend
        model = ""
        try:
            model = backend.checkpoint() or ""
        except Exception:
            # Never let a label stop an image being recorded.
            model = ""
        self.catalog.record(
            target, prompt, source=source,
            transcript=self.state.get("transcript", ""),
            backend=self.s.get("image.backend", "comfyui") or "",
            model=model)
        return target

    # ---- overlay -------------------------------------------------------

    def animate_image(self, source, on_progress=None):
        """
        Turn one still into a clip. Blocking; callers use a thread.

        Deliberately nothing to do with the listening cycle. A clip
        takes about two minutes, and the live loop puts a picture up
        every twenty seconds - they cannot share a queue without one
        of them starving.
        """
        from .video import VideoBackend

        source = Path(source)

        # A clip can take the GPU down with it - an illegal memory
        # access kills ComfyUI outright - and the next attempt then
        # meets a refused connection, which reads as the app being
        # broken rather than as something needing restarting. The
        # listening cycle has always brought it back; this path never
        # did.
        if not self._ensure_comfy():
            raise GenerationError(
                "ComfyUI is not running and could not be started. It "
                "sometimes stops after a clip that asked too much of "
                "the graphics card - try a shorter one.")

        backend = VideoBackend(self.s)
        if not backend.available():
            raise GenerationError(
                f"The video model is not installed yet "
                f"({backend.checkpoint()}).")

        out_dir = self.s.dir_for("paths.overlay_dir", "output")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        target = out_dir / f"clip_{stamp}.webm"

        self._video_cancel = False
        clip = backend.animate(
            source, target,
            cancel=lambda: self._video_cancel,
            on_progress=on_progress)
        if clip is None:
            return None

        # Recorded like any other output, carrying where it came from so
        # the gallery can say "animated from that picture".
        entry = self.catalog.lookup(source) or {}
        frames = int(self.s.get("video.frames", 25))
        fps = max(1, int(self.s.get("video.fps", 10)))
        self.catalog.record(
            clip, entry.get("prompt") or f"animated from {source.name}",
            source="video",
            transcript=entry.get("transcript", ""),
            backend="svd",
            model=backend.checkpoint(),
            seconds=frames / fps)
        return clip

    def cancel_video(self):
        """Stop a clip part way. What has been sampled is discarded."""
        self._video_cancel = True
        try:
            from .video import VideoBackend

            VideoBackend(self.s).interrupt()
        except Exception:
            pass

    def clear_overlay(self):
        """
        Take whatever is showing off the overlay, leaving nothing.

        For the moment an image turns up that should not be on stream. The
        file is left exactly where it was written - on disk and in the
        gallery - because this is about what is visible, not about
        deleting anything. It can be put back from the gallery.

        Listening carries on, so the next cycle will put a new image up.
        Stopping as well would be a second decision, and the button says
        one thing.
        """
        self._set(image=None, image_source="", overlay_cleared=True)
        self._write_state_json()
        return True

    def _allowed_on_overlay(self, path):
        """
        Whether a freshly made picture may go on the overlay.

        Three answers, not two. Clearly safe goes straight up. Clearly
        not is held back and marked, so it is blurred in the gallery
        rather than lost. Unsure is marked too but still shown - being
        blocked by a maybe, in the middle of a stream, is its own kind
        of failure.

        Only consulted while safe mode is on; off, this never runs and
        costs nothing.
        """
        from .safety import is_on, verdict

        if not is_on(self.s):
            return True

        found = verdict(path)
        if found == "safe":
            return True

        # Marked in the catalogue, which is what makes the gallery blur
        # it and the Censored filter find it.
        try:
            self.catalog.set_censored(path, True)
        except Exception:
            pass

        if found == "unsafe":
            self._set(status="Held back - safe mode")
            return False
        return True

    def publish_overlay(self, path, source="manual"):
        """
        Put an image on the overlay. Live cycles call this automatically;
        manual images route through here too, so a later live image simply
        overwrites a manual one - there's no lock to get stuck in.

        The web page asks for images by name from the overlay folder, so
        anything living elsewhere - a manual image in the user's save
        folder, or one picked from the gallery after its mirror was
        pruned - is copied in first. Without that, showing it would put a
        name in state.json that the server answers with a 404, and the
        overlay would simply go blank.
        """
        path = Path(path)
        if not path.exists():
            return False

        out_dir = self.s.dir_for("paths.overlay_dir", "output")
        try:
            if path.parent.resolve() != out_dir.resolve():
                mirror = out_dir / path.name
                if not mirror.exists():
                    shutil.copy2(path, mirror)
                path = mirror
        except OSError as exc:
            self._set(error=f"Could not prepare {path.name} for the "
                            f"overlay: {exc}")
            return False

        self._set(image=str(path), image_source=source,
                  pending_manual=None, overlay_cleared=False)
        self._write_state_json()
        self._prune()
        return True

    def _write_state_json(self):
        """The overlay page polls this."""
        out_dir = self.s.dir_for("paths.overlay_dir", "output")
        snap = self.snapshot()
        img = snap.get("image")
        payload = {
            "image": Path(img).name if img else None,
            "source": snap.get("image_source", ""),
            "cleared": bool(snap.get("overlay_cleared")),
            "prompt": snap.get("prompt", ""),
            "transcript": snap.get("transcript", ""),
            "status": snap.get("status", ""),
            # Whether the picture has a transparent background. The page
            # needs to know two things from it: not to crop the subject
            # to fill the screen, which "cover" would do, and that the
            # picture is meant to float rather than to cover the scene.
            "cutout": bool(self.s.get("image.cutout", False)),
            "updated": time.time(),
        }
        tmp = out_dir / "state.json.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(out_dir / "state.json")

    def prunable_count(self, keep=None):
        """
        How many images a given "keep last" limit would delete.

        Favourites are excluded, since pruning never touches them, and so
        is whatever is currently on the overlay. Used to warn before a
        lower limit is applied rather than after the files are gone.
        """
        if keep is None:
            keep = int(self.s.get("image.keep_images", 40))
        if keep <= 0:
            return 0
        out_dir = self.s.dir_for("paths.overlay_dir", "output")
        kept = self.catalog.favourites()
        current = self.state.get("image")
        ordinary = [
            p for p in out_dir.glob("*")
            if p.suffix.lower() in (".png", ".jpg", ".jpeg")
            and p.name not in kept
            and str(p) != (current or "")
        ]
        return max(0, len(ordinary) - keep)

    def prune_now(self):
        """Apply the current limit straight away, and say what went."""
        before = self.prunable_count()
        self._prune()
        return before

    def _prune(self):
        """Keep the overlay folder from growing without bound."""
        keep = int(self.s.get("image.keep_images", 40))
        if keep <= 0:
            return
        out_dir = self.s.dir_for("paths.overlay_dir", "output")
        imgs = sorted(
            [p for p in out_dir.glob("*") if p.suffix.lower() in
             (".png", ".jpg", ".jpeg")],
            key=lambda p: p.stat().st_mtime, reverse=True)
        current = self.state.get("image")
        # Favourites do not count towards the limit and are never removed:
        # that is the whole point of marking one.
        kept = self.catalog.favourites()
        ordinary = [p for p in imgs if p.name not in kept]
        for old in ordinary[keep:]:
            if current and str(old) == current:
                continue
            try:
                old.unlink()
                # The image is gone, so its catalogue entry is dead weight.
                self.catalog.forget(old)
            except OSError:
                pass

    # Characters Windows refuses in a filename, plus the path separators.
    _BAD_NAME = set('<>:"/\\|?*')

    def rename_image(self, path, new_stem):
        """
        Rename an image on disk, keeping everything attached to it.

        Returns (ok, message). The catalogue is keyed by filename, so the
        entry has to move too or the prompt and the favourite mark are
        lost. Both copies of a manual image are renamed, and if the image
        is currently on the overlay the page is pointed at the new name -
        otherwise it would keep asking for a file that no longer exists.
        """
        path = Path(path)
        stem = " ".join((new_stem or "").split())
        if not stem:
            return False, "A name is needed."
        if any(ch in self._BAD_NAME for ch in stem):
            return False, 'A name cannot contain < > : " / \\ | ? *'
        if stem == path.stem:
            return False, ""

        new_name = stem + path.suffix
        folders = {path.parent,
                   self.s.dir_for("paths.overlay_dir", "output")}

        # Nothing is moved until every destination is known to be free,
        # so a clash cannot leave one copy renamed and the other not.
        for folder in folders:
            if (folder / path.name).exists() and (folder / new_name).exists():
                return False, f"{new_name} already exists."

        moved = []
        try:
            for folder in folders:
                source = folder / path.name
                if source.exists():
                    source.rename(folder / new_name)
                    moved.append(folder / new_name)
        except OSError as exc:
            for done in moved:            # put back what was already moved
                try:
                    done.rename(done.parent / path.name)
                except OSError:
                    pass
            return False, f"Could not rename: {exc}"

        self.catalog.rename(path, path.parent / new_name)

        if str(path) == (self.state.get("image") or ""):
            self._set(image=str(path.parent / new_name))
            self._write_state_json()
        return True, new_name

    def set_censored(self, path, value=True):
        """
        Blur an image in the gallery, or stop blurring it.

        Purely a display choice: the file is untouched, so censoring is
        always reversible and never costs the picture.
        """
        self.catalog.set_censored(path, value)
        self._set(error="")
        return True

    def purgeable(self):
        """
        Every image that a purge would remove, deduplicated by name.

        Favourites are excluded, for the same reason pruning skips them:
        marking one is the user saying to keep it, and a bulk action is
        exactly where that promise matters most.
        """
        kept = self.catalog.favourites()
        folders = {self.s.dir_for("paths.overlay_dir", "output"),
                   self.s.dir_for("paths.manual_dir", "output")}
        found = {}
        for folder in folders:
            if not folder.exists():
                continue
            for candidate in folder.iterdir():
                if candidate.suffix.lower() not in (".png", ".jpg",
                                                    ".jpeg", ".webp"):
                    continue
                if candidate.name.startswith("_"):
                    continue
                if candidate.name in kept:
                    continue
                found.setdefault(candidate.name, candidate)
        return sorted(found.values())

    def purge_images(self):
        """
        Delete every image that is not a favourite.

        Returns (removed, failed). The overlay is cleared first if what it
        is showing is about to go, and the catalogue entries go with the
        files - a record pointing at nothing is worse than no record.
        """
        doomed = self.purgeable()
        if not doomed:
            return 0, 0

        current = self.state.get("image") or ""
        if current and Path(current).name in {p.name for p in doomed}:
            self.clear_overlay()

        folders = {self.s.dir_for("paths.overlay_dir", "output"),
                   self.s.dir_for("paths.manual_dir", "output")}
        removed = failed = 0
        for image in doomed:
            gone = False
            for folder in folders:
                candidate = folder / image.name
                try:
                    if candidate.exists():
                        candidate.unlink()
                        gone = True
                except OSError:
                    failed += 1
            if gone:
                self.catalog.forget(image)
                removed += 1

        if failed:
            self._set(error=f"{failed} image(s) could not be deleted.")
        else:
            self._set(error="")
        return removed, failed

    def delete_image(self, path):
        """
        Remove an image from disk and from the catalogue.

        If it happens to be what the overlay is showing, the overlay is
        cleared first - otherwise the page would keep asking for a file
        that no longer exists.
        """
        path = Path(path)
        if str(path) == (self.state.get("image") or ""):
            self.clear_overlay()

        removed = []
        # A manual image exists in two places: where it was saved and as a
        # copy in the overlay folder. Deleting one and leaving the other
        # would look like the delete silently failed.
        for folder in {path.parent,
                       self.s.dir_for("paths.overlay_dir", "output")}:
            candidate = folder / path.name
            try:
                if candidate.exists():
                    candidate.unlink()
                    removed.append(candidate)
            except OSError as exc:
                self._set(error=f"Could not delete {candidate.name}: {exc}")
                return False

        self.catalog.forget(path)
        self._set(error="")
        return bool(removed)

    # ---- manual --------------------------------------------------------

    def _run_manual(self, job):
        """
        Render a manual job. Listening is already paused by virtue of this
        running on the one worker thread.
        """
        try:
            self._ensure_ready()
        except Exception as exc:
            self._set(mode="idle", busy=False, error=str(exc))
            return

        auto = job.auto_push
        if auto is None:
            auto = bool(self.s.get("image.manual_auto_push", True))

        if job.save_dir:
            save_dir = Path(job.save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Blank means the Desktop, which dir_for cannot guess, so the
            # fallback is resolved here.
            save_dir = self.s.dir_for("paths.manual_dir", str(_desktop()))

        self._set(prompt=job.prompt, error="", skip_reason="")
        last = None

        for i in range(job.amount):
            if self._shutdown.is_set():
                break
            if job.amount > 1:
                self._set(status=f"Generating {i + 1} of {job.amount}...")

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = save_dir / f"ai_{stamp}_{i + 1}.{self._backend.extension}"

            # The same refusal the listening cycle makes. This path had
            # none of it: safe mode steered the negative prompt and
            # checked the picture afterwards, but a typed prompt asking
            # outright was generated without comment - which is what
            # "safe mode does not seem to work" turned out to mean.
            from .safety import is_on as _safe_mode
            from .safety import prompt_is_blocked

            if _safe_mode(self.s) and prompt_is_blocked(job.prompt):
                self._set(mode="idle", busy=False,
                          status="Refused - safe mode is on")
                return

            got = self._render(job.prompt, source="manual", dest=dest)
            if got is None:
                break   # cancelled or failed; _render already reported it
            last = got

            # publish_overlay handles getting the file into the overlay
            # folder, so there is one copy of that logic rather than two.
            if auto and self._allowed_on_overlay(got):
                self.publish_overlay(got, source="manual")
            elif i == job.amount - 1:
                self._set(pending_manual=str(got))

        if last is not None:
            self._set(mode="idle", busy=False,
                      status=f"Saved to {save_dir}")
            # Typed prompts go into the same history, so Repeat works on
            # whichever came last rather than only on overheard speech.
            self._remember(job.prompt, prompt=job.prompt, image=last,
                           kind="typed", base=job.base, suffix=job.suffix)

    def push_pending(self):
        """Send the last manual image to the overlay (auto-push off)."""
        pending = self.state.get("pending_manual")
        if not pending:
            return False
        # Pushed by hand, but still checked: the button is a decision to
        # show it, not a decision to skip the check.
        if not self._allowed_on_overlay(pending):
            return False
        return self.publish_overlay(pending, source="manual")
