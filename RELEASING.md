# Releasing WispWasp

This is the release checklist. The deeper history behind several of these
rules lives in [SETUP.md](SETUP.md).

## Before the version change

1. Start from a clean, up-to-date `main`.
2. Confirm the normal CI gate is green.
3. Review dependency and licensing changes:
   - `requirements-lock.txt`
   - `wispwasp.spec`
   - bundled binaries/assets
   - [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
4. Run any hardware/live integration checks relevant to the changed area.

## Version and release notes

`avcore/version.py` is the canonical application version.

For a release:

1. update `__version__`;
2. update the user-facing `CHANGES` list;
3. do not hand-copy the version into unrelated source files;
4. build from the exact commit intended for the release.

`latest.json` describes the **published installer**, so its SHA-256 and size
must come from the final artifact, not from an earlier local build.

## Build

Run:

```powershell
.\build.ps1
```

The build path should:

- run `tools\run_tests.py`;
- create the PyInstaller application;
- run the packaged `--selftest`;
- verify bundled legal notices;
- create the installer.

Do not use `-SkipTests` or `-SkipSelfTest` for a public release unless the
release notes explicitly document why the normal gate could not be used.

## Commit and push before tagging

The safe order is:

1. build and test;
2. commit;
3. push;
4. confirm the remote branch contains the intended release commit;
5. only then create the tag/release.

A tag created from an unpushed local state can point at the wrong commit.

## Publish

Each GitHub Release should contain at least:

- `WispWasp-<version>-setup.exe`;
- `installer/READ-ME-FIRST.md`;
- `latest.json`.

After the installer is final, calculate its SHA-256 and byte size and update
`latest.json` to match the published filename and version.

If GitHub repeatedly returns HTTP 500 while uploading the exact same asset
name, delete/recreate the release or use a clean release record rather than
blindly retrying forever. A failed upload can leave a ghost asset record.

## Verify the public path

Do not stop at "upload succeeded."

From an unauthenticated/public path:

1. fetch the published `latest.json`;
2. confirm it reports the intended version;
3. download the installer URL from that manifest;
4. compare the downloaded file's SHA-256 and byte size with the manifest;
5. confirm the release page is reachable;
6. confirm the updater only informs the user and does not execute the
   installer automatically.

## After release

- leave older releases available for rollback;
- open the installed app and confirm the displayed version;
- keep source recovery in Git history rather than a hard-coded local backup
  folder;
- if dependencies, bundled assets, models, or licensing terms changed, verify
  that LICENSE/THIRD_PARTY_NOTICES and installer notices still describe what
  is actually shipped.
