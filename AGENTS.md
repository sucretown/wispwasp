# AGENTS.md

These instructions apply to the entire repository.

WispWasp is developed quickly, often with coding agents. Optimize for
**correct, reviewable changes that preserve the existing runtime model**, not
for the largest refactor an agent can produce.

## Read before changing code

1. Read [README.md](README.md).
2. Read [ARCHITECTURE.md](ARCHITECTURE.md).
3. Read the relevant section of [SETUP.md](SETUP.md) when touching Windows
   audio, Qt, ComfyUI, setup/downloads, packaging, releases, or an existing
   workaround.

Existing comments frequently record a failure that took real debugging to
isolate. Do not delete a "why" comment unless the failure mode it describes is
actually removed.

## Change discipline

- Keep one problem per branch/PR.
- Make the smallest coherent change that solves the problem.
- Do not reorganize unrelated modules while implementing a feature.
- Do not edit `legacy/` to implement current behavior.
- Do not introduce a second source of truth for engine state or version data.
- Do not add a dependency when the standard library or an existing dependency
  already solves the problem cleanly.
- Do not swallow exceptions merely to make an error disappear. Convert them to
  useful state/messages at the boundary where recovery is possible.
- Give network and subprocess operations explicit timeouts or shutdown paths
  where applicable.
- Preserve user files and partial downloads on recoverable failures unless the
  existing contract says otherwise.

## Architecture constraints

- `avcore/` must remain usable without constructing Qt widgets.
- `avgui/` may call core behavior; core should not call into GUI modules.
- `Engine` owns runtime orchestration and state snapshots.
- `Settings` owns persisted configuration.
- `app.py` owns process startup/shutdown.
- `avcore/version.py` is the canonical version source.
- Slow work must not block the Qt main thread.
- ComfyUI is an external process/API, not an in-process library.

If a requested change appears to violate one of these, explain the conflict in
the PR and prefer adjusting the design over punching through the boundary.

## Tests are part of the change

For a bug, add or strengthen a regression test whenever practical.

Run the nearest test while iterating. Then run:

```powershell
.\.venv\Scripts\python.exe tools\run_tests.py
```

`tools/run_tests.py` is the canonical normal gate. It gives each suite an
isolated temporary `WISPWASP_DATA_DIR`, so tests must not depend on settings
or runtime files left by another suite. Tests in that list must not require a
physical microphone, loopback device, GPU, live internet service, or
developer-specific files.

Use the explicit hardware/live integration suites — including
`test_mic.py`, `test_process_audio.py`, `test_real_*.py`,
`test_live_download.py`, and `test_full_install.py` — for scenarios that
genuinely require those resources. They are intentionally outside the normal
deterministic gate.

## Git and generated data

Never commit local settings, generated output, model weights, build output,
logs, caches, temporary test folders, or local backup snapshots.

Source-mode runtime state belongs under `.wispwasp-data/`. If a change starts
creating `settings.json`, `prompts.json`, `styles.json`, `sounds/`, or
other runtime state at repository root again, fix the path rather than adding
the generated files to Git.

Before adding a new generated/binary artifact, check whether it belongs in a
release asset or can be reproduced from source instead.

## Release-sensitive files

Changes to any of these deserve explicit PR notes and full build verification:

- `avcore/version.py`
- `latest.json`
- `build.ps1`
- `wispwasp.spec`
- `installer.iss`
- `requirements-lock.txt`
- `LICENSE`
- `THIRD_PARTY_NOTICES.md`
- `installer/LICENSE-NOTICE.txt`

Do not bump a version just because code changed. Version changes belong to the
release step.

## When unsure

Prefer an explicit assumption plus a focused test over speculative
architecture. Do not "future-proof" the code by adding abstractions with no
current caller.
