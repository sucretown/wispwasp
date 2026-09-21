## What changed

<!-- Describe the behavior change, not just the files touched. -->

## Why

<!-- What problem or limitation does this solve? -->

## Verification

<!-- Exact focused test(s), full build, manual scenario, screenshots, etc. -->

## Risk / regression surface

<!-- What existing behavior is most likely to be affected? -->

### Checklist

- [ ] The branch contains one focused change.
- [ ] I ran the closest regression/behavior suite.
- [ ] New failure behavior has coverage where practical.
- [ ] I did not commit generated output, local settings, logs, models, or build artifacts.
- [ ] Source-mode runtime data still stays under `.wispwasp-data/`.
- [ ] UI state still comes from the engine/settings rather than a new competing source of truth.
- [ ] Version information is derived from `avcore/version.py` unless this is the release-version change itself.
- [ ] New dependencies/bundled assets were checked for licensing and redistribution implications.
- [ ] Documentation was updated when user-visible behavior, setup, architecture, or release procedure changed.
