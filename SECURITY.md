# Security policy

## Reporting a vulnerability

Do not post credentials, private logs, API keys, or a working exploit in a
public issue.

If GitHub shows **Report a vulnerability** on the repository's Security tab,
use that private reporting path. Otherwise contact the repository maintainer
privately through their GitHub profile before publishing sensitive details.

For an ordinary bug that has no security impact, use the normal bug-report
issue template instead.

## Useful report contents

Include:

- WispWasp version and commit, if known;
- Windows version;
- whether the affected path uses local ComfyUI or an online service;
- the smallest reproducible sequence;
- expected and actual behavior;
- relevant exception text with secrets and personal paths redacted.

Do **not** attach `settings.json` without inspecting it first. It may contain
a Civitai API key and machine-specific paths.

## Security-sensitive areas

Changes in these areas deserve extra review:

- model/archive downloads and extraction;
- path handling in the local overlay server;
- subprocess launch and process termination;
- settings/profile handling of API keys;
- update/download URLs and integrity checks;
- file deletion/rename operations in the gallery or setup UI;
- bundled executables and release artifacts.

The overlay server is intentionally bound to loopback. A change that exposes it
to other network interfaces is security-relevant and should not be treated as a
cosmetic configuration change.
