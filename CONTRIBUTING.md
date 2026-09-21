# Contributing to WispWasp

WispWasp moves quickly. The goal of these rules is not ceremony; it is to keep a fast idea stream from turning into hidden coupling, lost work, or releases that cannot be reproduced.

## One change, one branch

Start from an up-to-date `main`:

```powershell
git switch main
git pull --ff-only
git switch -c fix/short-description
```

Use a prefix that says what kind of work is happening: `fix/`, `feature/`, `chore/`, `docs/`, or `refactor/`.

Avoid mixing unrelated cleanup into a feature branch. Small branches are easier to understand, test, revert, and merge.

## Before writing code

Identify which layer owns the behavior.

- **`avcore.engine.Engine` owns runtime application state.** UI panels observe snapshots and request actions.
- **`avcore/` owns non-visual behavior.** Keep Qt/widget decisions out of core modules where practical.
- **`avgui/` owns presentation and interaction.** A panel should not create a second source of truth for engine state.
- **`app.py` owns process lifetime/bootstrap.**
- **`legacy/` is historical.** Do not build new features there.
- **`avcore/version.py` is the canonical application version.** Import/derive it rather than typing another copy.

If a proposed feature crosses several of these boundaries, write down the data flow first. A short diagram or paragraph is cheaper than untangling an accidental dependency later.

## Fixes need a reproducer

When possible, add the failing case to the closest existing `test_*.py` suite before or alongside the fix.

Examples:

- overlay/server behavior → `test_server.py`
- engine state/queueing → `test_engine.py`
- installation/download logic → `test_setup.py` or `test_install.py`
- settings/UI behavior → the closest settings/layout/customize suite
- documentation invariants → `test_docs.py`

Run the focused suite repeatedly while working. Before publishing a release, run `.\build.ps1`.

## Git habits that prevent expensive mistakes

Inspect what you are about to commit:

```powershell
git status
git diff
git add -p
git diff --cached
git commit
git push -u origin HEAD
```

Commit messages should describe the consequence or reason, not only the files changed. The existing history does this well; keep that property.

Do not force-push shared branches unless everyone using the branch expects the history rewrite. Never develop directly in `main` when a branch will do.

## Keep generated/local data out of Git

Do not commit:

- `.venv/`, build output, installer binaries, or model weights;
- `settings.json` or generated `output/`;
- scratch/test folders beginning with `_`;
- log files, coverage output, caches, or editor-local state.

If a generated artifact is required for the application itself, document why it belongs in source control.

## Preserve useful failure knowledge

This codebase contains comments that explain non-obvious Windows, Qt, ComfyUI, packaging, and release behavior. Do not remove those comments merely because the current code looks obvious.

When replacing a workaround, remove its explanation only after the underlying failure mode is no longer possible.

## Pull requests

A pull request should say:

- what changed;
- why it changed;
- how it was tested;
- what can regress;
- whether setup, packaging, release metadata, or user data formats changed.

For UI work, include a screenshot when it makes review materially easier.

## Releases

Normal feature/fix pull requests should not bump the version. When preparing an
actual release, follow [RELEASING.md](RELEASING.md) so versioning, build
verification, artifact hashes, public download checks, and licensing review
happen in one defined order.
