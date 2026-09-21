"""
Self-check for a packaged build.

Every piece here is something that works from source but can silently go
missing once frozen: a native module PyInstaller did not spot, a data file
left out of the bundle. Running this on a machine that is misbehaving is
far quicker than guessing.

    WispWasp.exe --selftest
"""

import sys
import tempfile
from pathlib import Path


def _write(lines, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run(log_path):
    lines = []
    ok = True

    def record(name, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        mark = "ok  " if passed else "FAIL"
        lines.append(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))

    lines.append("WispWasp self-test")
    lines.append(f"frozen: {getattr(sys, 'frozen', False)}")
    lines.append(f"python: {sys.version.split()[0]}")
    lines.append("")

    # --- imports that have gone missing in a frozen build before --------
    # py7zr is deliberately absent: extraction uses the bundled 7zr.exe,
    # which is checked separately below.
    for mod in ("PySide6.QtWidgets", "faster_whisper", "ctranslate2",
                "onnxruntime", "pyaudiowpatch", "flask", "numpy",
                "requests"):
        try:
            __import__(mod)
            record(f"import {mod}", True)
        except Exception as exc:
            record(f"import {mod}", False, str(exc))

    # --- the VAD model, which vad_filter cannot run without -------------
    try:
        import faster_whisper
        assets = Path(faster_whisper.__file__).parent / "assets"
        found = list(assets.glob("*.onnx")) if assets.exists() else []
        record("silero VAD model is bundled", bool(found),
               found[0].name if found else f"nothing in {assets}")
    except Exception as exc:
        record("silero VAD model is bundled", False, str(exc))

    # --- bundled assets --------------------------------------------------
    # The icons and the easter-egg clip live in the assets folder. The
    # spec copies the whole folder, so this is a check that the copy
    # happened rather than that any one file was listed - which is the
    # shape of mistake that took QtMultimedia out of a build once.
    try:
        from avgui.assets import asset_dir

        folder = asset_dir()
        icons = list(folder.glob("*.png")) if folder.exists() else []
        record("bundled assets are present", bool(icons),
               f"{len(icons)} images in {folder}")
        clip = folder / "well_do_it_live.wav"
        record("the version-click clip is bundled", clip.exists(),
               clip.name if clip.exists() else f"missing from {folder}")
    except Exception as exc:
        record("bundled assets are present", False, str(exc))

    # --- video, which is optional but should work if claimed ----------
    # PyAV is deliberately excluded and stubbed, and nothing in the app
    # decodes video: posters are written by ComfyUI when the clip is
    # made, and a clip's length is recorded in the catalogue.

    try:
        from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: F401

        record("video playback is bundled", True)
    except Exception as exc:
        record("video playback is bundled", False, str(exc))

    # --- overlay page ---------------------------------------------------
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    record("overlay.html is bundled", (base / "overlay.html").exists(),
           str(base / "overlay.html"))
    for notice in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        record(f"{notice} is bundled", (base / notice).exists(),
               str(base / notice))

    # --- 7z round trip, which is what setup depends on ------------------
    # This is the one worth proving: extraction happens after a 1.8 GB
    # download, so a missing or broken extractor wastes all of it.
    try:
        from avcore.setup import seven_zip_exe
        exe = seven_zip_exe()
        record("7-Zip extractor is bundled", exe is not None and exe.exists(),
               str(exe))
        if exe and exe.exists():
            import subprocess
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                (td / "src").mkdir()
                (td / "src" / "hello.txt").write_text("x" * 5000)
                archive = td / "t.7z"
                subprocess.run(
                    [str(exe), "a", str(archive), "src"], cwd=str(td),
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                out = td / "out"
                subprocess.run(
                    [str(exe), "x", str(archive), f"-o{out}", "-y"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                got = (out / "src" / "hello.txt")
                record("7z compress and extract",
                       got.exists() and got.read_text() == "x" * 5000)
            # BCJ2 is what the real ComfyUI archive uses, and what the
            # pure-Python extractor could not handle.
            codecs = subprocess.run(
                [str(exe), "i"], capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            record("extractor supports BCJ2",
                   "BCJ2" in (codecs.stdout or ""))
    except Exception as exc:
        record("7z compress and extract", False,
               f"{type(exc).__name__}: {exc}")

    # --- the PyAV stub, which replaced the real thing --------------------
    try:
        import av
        try:
            av.open("nothing")
            record("PyAV stub refuses real use", False,
                   "it did not raise")
        except RuntimeError:
            record("PyAV stub refuses real use", True)
        except Exception as exc:
            record("PyAV stub refuses real use", False, str(exc))
    except Exception as exc:
        record("PyAV stub is importable", False, str(exc))

    # --- audio devices ---------------------------------------------------
    try:
        from avcore.audio import INPUT, OUTPUT, list_devices
        outputs = list_devices(OUTPUT)
        inputs = list_devices(INPUT)
        record(f"loopback devices found", bool(outputs),
               f"{len(outputs)} output(s)")
        record(f"microphones found", bool(inputs),
               f"{len(inputs)} input(s)")
    except Exception as exc:
        record("audio devices found", False, str(exc))

    # --- theme sounds -----------------------------------------------------
    # Optional, but the packaged build once excluded QtMultimedia in the
    # spec, so turning sounds on did nothing and nothing said why.
    try:
        from PySide6.QtMultimedia import QSoundEffect
        record("sound backend present", QSoundEffect is not None,
               "QtMultimedia")
    except ImportError as exc:
        record("sound backend present", False,
               f"theme sounds will stay silent: {exc}")

    # --- per-application capture -----------------------------------------
    # Entirely COM, so a packaged build can lose it in ways that only show
    # up when someone selects an app and gets silence.
    try:
        from avcore.process_audio import audible_processes, is_supported
        ok_proc, why = is_supported()
        record("per-app capture supported by this Windows", ok_proc, why)
        if ok_proc:
            apps = audible_processes()
            record("audio sessions can be enumerated", True,
                   f"{len(apps)} application(s)")
    except Exception as exc:
        record("per-app capture available", False,
               f"{type(exc).__name__}: {exc}")

    # --- what setup thinks is installed ----------------------------------
    try:
        from avcore import setup as avsetup
        from avcore.config import Settings, DATA_DIR
        rep = avsetup.check(Settings.load())
        lines.append("")
        lines.append(f"data folder : {DATA_DIR}")
        lines.append(f"gpu         : {rep['gpu_name']}")
        lines.append(f"comfyui     : "
                     f"{rep['root'] if rep['comfy_ok'] else 'not installed'}")
        lines.append(f"models      : "
                     f"{[p.name for p in rep['models']] or 'none'}")
        lines.append(f"free space  : {avsetup.human(rep['free_bytes'])}")
        lines.append(f"ready       : {rep['ready']}")
    except Exception as exc:
        record("setup check", False, str(exc))

    lines.append("")
    lines.append("RESULT: everything passed" if ok
                 else "RESULT: something is wrong, see the FAIL lines above")
    _write(lines, log_path)
    return ok, "\n".join(lines)
