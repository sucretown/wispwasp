# PyInstaller build spec for WispWasp.
#
#   .venv\Scripts\pyinstaller.exe WispWasp.spec --noconfirm
#
# Notes on why things are here:
#   - faster_whisper ships silero_vad_v6.onnx as package data. vad_filter
#     needs it, and without collect_data_files it is silently left out.
#   - ctranslate2 and onnxruntime are native; their DLLs come in via
#     collect_dynamic_libs.
#   - torch is not a dependency of this app at all (ComfyUI has its own
#     environment), so it is excluded to stop it being dragged in.

from PyInstaller.utils.hooks import (
    collect_all, collect_data_files, collect_dynamic_libs,
    collect_submodules,
)

datas = [("overlay.html", "."), ("tools/7zr.exe", "tools"),
         ("assets", "assets"), ("LICENSE", "."),
         ("THIRD_PARTY_NOTICES.md", ".")]
binaries = []
hiddenimports = []

# The VAD model and tokenizer assets.
datas += collect_data_files("faster_whisper")

for pkg in ("ctranslate2", "onnxruntime"):
    binaries += collect_dynamic_libs(pkg)

hiddenimports += collect_submodules("avcore")
hiddenimports += collect_submodules("avgui")
hiddenimports += ["selftest"]

# Video playback and the poster frames the gallery draws for clips.
# PyAV is imported lazily inside avcore.video, so PyInstaller cannot see
# it by following imports, and QtMultimedia is only reached through
# strings in the player.
hiddenimports += collect_submodules("av")

# Naming these as hidden imports is not enough on its own: the PySide6
# hook copies the Qt DLLs for modules it can see being used, and the
# player reaches QtMultimediaWidgets only through a function-level
# import. Without collecting it, QtMultimedia arrives (the easter egg
# uses it) while QtMultimediaWidgets does not, and video playback fails
# in the packaged build while working perfectly from source.
for _module in ("PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"):
    _extra_datas, _extra_binaries, _extra_hidden = collect_all(_module)[:3]
    datas += _extra_datas
    binaries += _extra_binaries
    hiddenimports += _extra_hidden

# Per-application capture is built on COM. comtypes generates interface
# code at runtime and pycaw enumerates audio sessions; neither is reachable
# by static analysis from the imports alone.
hiddenimports += collect_submodules("comtypes")
hiddenimports += collect_submodules("pycaw")
datas += collect_data_files("comtypes")
hiddenimports += [
    "pyaudiowpatch",
    "onnxruntime",
    "onnxruntime.capi._pybind_state",
    "ctranslate2",
    "tokenizers",
    "flask",
    "werkzeug",
    "jinja2",
    "click",
    "itsdangerous",
    "blinker",
]

excludes = [
    "torch", "torchvision", "torchaudio",
    "tkinter", "matplotlib", "scipy", "pandas", "IPython",
    "pytest", "unittest", "pydoc_data",
    # PyAV is imported by faster-whisper but never exercised, since
    # avcore.speech decodes its own WAVs. A runtime hook supplies a stub
    # that raises if anything ever does need it. Saves about 62 MB.
    "av",
    # Qt modules this app never touches. PySide6-Essentials still bundles
    # a fair amount; dropping these saves well over a hundred megabytes.
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools",
    # QtMultimedia is NOT excluded: the theme sound effects need it, and
    # excluding it here silently produced a build where turning sounds on
    # did nothing at all. QtMultimediaWidgets is not excluded either, for
    # the same reason one step along - the gallery's clip player draws
    # into a QVideoWidget, and with it excluded the player failed only in
    # the packaged build while working perfectly from source.
    "PySide6.QtCharts", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtWebSockets", "PySide6.QtWebChannel",
    "PySide6.QtSerialPort", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.Qt3DCore",
    "PySide6.QtDataVisualization", "PySide6.QtSpatialAudio",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=["hooks/rthook_av_stub.py"],
    excludes=excludes,
    noarchive=False,
)

# Qt ships translation catalogues for dozens of languages. The app is
# English-only, so they are dead weight.
a.datas = [d for d in a.datas
           if not (d[0].replace("\\", "/").startswith("PySide6/translations")
                   or d[0].endswith(".qm"))]

# opengl32sw.dll is deliberately kept. It is 20 MB of software OpenGL
# fallback that a machine with a working GPU driver never loads, but
# without it Qt can fail to start on systems with no usable driver -
# remote sessions, virtual machines, a friend with a broken install. A
# blank window that cannot be reproduced locally is not worth 20 MB.

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WispWasp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX trips antivirus heuristics on unsigned builds
    console=False,      # windowed; unhandled errors go to the crash log
    icon="assets/wispwasp.ico",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="WispWasp",
)
