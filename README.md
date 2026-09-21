# WispWasp

WispWasp is a Windows desktop application that listens to audio, transcribes it, turns the transcript into an image prompt, and publishes the newest generated image to a local overlay for OBS.

Image generation can run locally through ComfyUI or use the online fallback supported by the application. The repository also contains the installer/build pipeline, packaged-app self-test, and the behavioral checks used while developing the application.

## Start here

For a source checkout:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\WISPWASP.bat --demo
```

`--demo` is the safest first run for development because it does not require a GPU, microphone, or ComfyUI.

A normal source run is:

```powershell
.\WISPWASP.bat
```

The installed application opens on Setup on first launch.

Source-mode runtime state is kept under the ignored `.wispwasp-data/`
folder, not mixed into tracked source files.

## Repository map

| Path | Responsibility |
|---|---|
| `app.py` | process bootstrap, crash logging, overlay-server lifetime |
| `avcore/` | settings, audio, transcription, generation, setup, models, server, runtime engine |
| `avgui/` | PySide6 presentation and user interaction |
| `overlay.html` | browser-source view served to OBS |
| `selftest.py` | packaged-build diagnostic checks |
| `test_*.py` | behavior/regression suites run before builds |
| `build.ps1` | tests, PyInstaller build, packaged self-test, installer build |
| `SETUP.md` | detailed operating notes, architecture decisions, and failure history |
| `RELEASING.md` | version/build/publish verification checklist |
| `legacy/` | historical implementation; do not add new application behavior here |
| `brand/` | branding source material and generation helpers |

The runtime state owner is `avcore.engine.Engine`. UI panels observe engine state and request actions; they should not become competing state stores.

## Development checks

Run the smallest relevant suite while working:

```powershell
.\.venv\Scripts\python.exe test_server.py
.\.venv\Scripts\python.exe test_engine.py
```

Run the complete normal, non-live behavior gate without packaging:

```powershell
.\.venv\Scripts\python.exe tools\run_tests.py
```

Before publishing a build, run the project build path:

```powershell
.\build.ps1
```

That runs the normal test suites, creates the application, runs the packaged self-test, and builds the installer. Hardware/live-network exercises such as `test_mic.py`, `test_process_audio.py`, `test_real_*.py`, `test_live_download.py`, and `test_full_install.py` are intentionally separate from the normal build gate.

Pull requests also run the normal behavior suites on a clean Windows runner.

## Working on the project

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the short runtime map and
[CONTRIBUTING.md](CONTRIBUTING.md) for the Git/review workflow. Coding agents
should also follow [AGENTS.md](AGENTS.md).

The short version:

1. one focused branch per change;
2. reproduce a bug in the nearest test before or with the fix;
3. preserve the engine/UI ownership boundary;
4. derive version information from `avcore/version.py`;
5. do not commit generated output, local settings, model files, logs, or build artifacts;
6. keep the explanation of *why* when a workaround exists for a Windows, Qt, ComfyUI, packaging, or release edge case.

For the deeper technical record, see [SETUP.md](SETUP.md).

## License and third-party software

The upstream project has **not declared an open-source license**. Public source
visibility and GitHub forking do not by themselves grant open-source reuse
rights. See [LICENSE](LICENSE) for the current project-level status and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party components.

## Support and security

Use [SUPPORT.md](SUPPORT.md) for diagnostic/reporting guidance. Security
issues should follow [SECURITY.md](SECURITY.md), especially before posting
credentials, private logs, API keys, or exploit details publicly.
