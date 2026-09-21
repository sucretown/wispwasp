# Support

## Before opening an issue

Run the packaged self-test when using an installed build:

```text
WispWasp.exe --selftest
```

It writes a report under `%USERPROFILE%\WispWasp\selftest.log`.

For a source checkout, run the focused test closest to the problem and, when
practical, the normal gate:

```powershell
.\.venv\Scripts\python.exe tools\run_tests.py
```

## Bug reports

A useful report includes:

- WispWasp version;
- Windows version;
- GPU model when the issue involves local generation;
- whether ComfyUI is portable or an existing install;
- local vs. online image backend;
- exact steps that reproduce the problem;
- the relevant error text or self-test failures.

Redact API keys, usernames, private file paths, transcripts, and any generated
content you do not want public.

## What belongs elsewhere

Security-sensitive reports should follow [SECURITY.md](SECURITY.md).

Questions about a third-party model, ComfyUI itself, Pollinations, Civitai, or
Hugging Face may ultimately belong with that project/service when WispWasp is
only surfacing their response. A WispWasp issue is still appropriate when the
application handles that response incorrectly or explains it poorly.
