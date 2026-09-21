# WispWasp architecture

This document is the short map. [SETUP.md](SETUP.md) contains the deeper
implementation history, operational notes, and the reasons behind many
platform-specific workarounds.

## Runtime flow

```text
audio source
    |
    v
Recorder / ProcessRecorder
    |
    v
Engine  ---->  Transcriber
  |               |
  |               v
  |          transcript / prompt
  |               |
  |               v
  +---------- image backend --------> generated image
  |                                    |
  |                                    v
  +------------------------------ publish_overlay()
                                       |
                                       v
                              overlay state + files
                                       |
                                       v
                                OverlayServer
                                       |
                                       v
                                  OBS/browser
```

The `Engine` is the center of the runtime. That is intentional.

## Layer ownership

### `avcore/` — behavior

Core code owns settings, audio capture, process capture, transcription,
generation backends, model/setup operations, overlay state, and the runtime
engine.

Core modules should not import widgets or make UI layout decisions. A useful
test for a new core feature is: **could this behavior still be exercised from
a script with no visible window?**

### `avgui/` — presentation and interaction

GUI code renders state and translates user actions into calls on the engine or
settings.

A widget may keep temporary presentation state such as selection, expansion,
animation, or an uncommitted edit. It should not become a second authoritative
copy of runtime state.

### `app.py` — process lifetime

The entry point owns startup/shutdown concerns: logging, settings bootstrap,
the local overlay server, demo stubs, and the Qt event loop.

### `overlay.html` + `avcore/server.py` — OBS boundary

OBS never needs access to arbitrary user folders. The engine mirrors/publishes
only the files the overlay needs, and the local server exposes that bounded
directory plus `state.json`.

### ComfyUI — external process

ComfyUI is not part of this Python process. WispWasp discovers/starts it when
configured and communicates through its HTTP API. Code should continue to
treat ComfyUI failure, restart, and slow startup as normal recoverable states.

## State and persistence

There are three different kinds of state. Do not mix them.

1. **Runtime state** lives in `Engine` and is exposed through snapshots.
2. **User configuration** lives in `Settings` and is persisted through its
   save path.
3. **Presentation state** belongs to widgets when it is only about how
   something is currently displayed.

The application version has one source of truth: `avcore/version.py`.

## Concurrency

WispWasp has several execution contexts:

- the Qt main thread for widgets;
- the engine worker for capture/transcription/generation sequencing;
- the overlay HTTP server thread;
- short-lived workers used by selected setup/update UI operations;
- ComfyUI as a separate process.

Rules:

- do not perform slow network, model, process-start, or generation work on the
  Qt main thread;
- do not mutate Qt widgets directly from a non-Qt worker;
- generation must remain serialized where the engine expects one GPU job;
- cancellation and shutdown paths are product behavior, not cleanup trivia.

## Where new work belongs

| Change | First place to look |
|---|---|
| capture/transcription/generation sequence | `avcore/engine.py` |
| audio devices / recording | `avcore/audio.py` |
| per-process audio | `avcore/process_audio.py` |
| local/online image generation | `avcore/images.py` |
| model catalogue/download | `avcore/models.py` |
| ComfyUI discovery/install/start | `avcore/setup.py`, `avcore/comfy_launcher.py` |
| persistent setting/default | `avcore/config.py` |
| OBS serving | `avcore/server.py`, `overlay.html` |
| screen/layout/control | `avgui/` |
| packaging/release | `wispwasp.spec`, `build.ps1`, `installer.iss` |

If a feature appears to require changing many unrelated rows in this table,
write down the data flow first. That usually exposes a smaller boundary.

## Refactoring rule

Large files are not automatically bad. `avgui/themes.py`, for example, is
large because it contains a substantial amount of theme drawing/style data.
Split a module when there is a useful ownership boundary, not merely because a
line-count tool dislikes it.

Prefer refactors that can be described as **one responsibility moved behind one
stable interface**. Avoid repo-wide reshuffles in the same branch as a feature
or bug fix.

## Testing boundary

`tools/run_tests.py` is the canonical normal behavior gate. It must stay
hardware- and live-network-independent.

Tests that intentionally need a physical audio device, GPU, live service,
large download, or full installed environment remain explicit integration
tests and are not silently promoted into the normal gate.
