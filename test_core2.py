"""Checks the speech prompt logic and the ComfyUI backend."""
import os
import time
from pathlib import Path

from avcore.config import APP_DIR, DATA_DIR, Settings
from avcore import speech, images

print("=== source runtime data stays out of the repository root ===")
assert DATA_DIR != APP_DIR
override = os.environ.get("WISPWASP_DATA_DIR")
if override:
    assert DATA_DIR == Path(override)
    print("  PASS     isolated test data root")
else:
    assert DATA_DIR.parent == APP_DIR
    assert DATA_DIR.name == ".wispwasp-data"
    print(f"  PASS     {DATA_DIR.name}")

print("\n=== prompt building (must stay verbatim) ===")
cases = [
    ("Bro, this is live? Put up two fingers.", True),
    ("We are, we are, we are, we are, we are, we are, we are, we are.", False),
    ("Thank you very much.", False),
    ("It's dead. It's dead. The mom is dead?", True),
    ("   ", False),
    ("hi", False),
]
for text, should_pass in cases:
    out = speech.build_prompt(text, min_words=3)
    ok = (out is not None) == should_pass
    verdict = "PASS" if ok else "*** WRONG ***"
    if out and text.strip() not in out:
        verdict = "*** NOT VERBATIM ***"
    print(f"  {verdict:<8} {text.strip()[:44]!r}")
    assert ok, f"unexpected result for {text!r}"

print("\n  suffix appends after the speech, never inside it:")
p = speech.build_prompt("a red barn", style_suffix="oil painting")
print(f"    {p!r}")
assert p == "a red barn, oil painting"

print("\n  long speech truncates but keeps the suffix:")
long = ("the old stone bridge crossed a wide river where herons waited "
        "among reeds while morning fog lifted slowly off the water and "
        "fishermen pushed their flat boats out from the muddy bank ")
p = speech.build_prompt(long, style_suffix="cinematic", max_chars=60)
assert p is not None, "varied long text should not be rejected"
assert p.endswith("cinematic"), "suffix was truncated away"
assert len(p) < 120
print(f"    {p!r}")
print(f"    ends with suffix, total {len(p)} chars")

print("\n  repetition guard still rejects a genuine loop:")
assert speech.build_prompt(" ".join(["word"] * 200)) is None
print("    200x 'word' -> rejected")

print("\n=== comfyui backend ===")
s = Settings.load()
be = images.make_backend(s)
print(f"  backend: {type(be).__name__}")
print(f"  reachable: {be.is_up()}")

if be.is_up():
    print(f"  checkpoints: {be.list_checkpoints()}")
    print(f"  resolved: {be.checkpoint()}")
    dest = Path("output/_coretest.png")
    t = time.time()
    try:
        be.generate("a stone lighthouse at dawn, wide shot", dest)
        print(f"  rendered in {time.time()-t:.1f}s -> "
              f"{dest.stat().st_size//1024} KB")
    except images.GenerationError as exc:
        print(f"  generate failed: {exc}")
else:
    print("  (ComfyUI not running - skipping render test)")

print("\nall checks passed")
