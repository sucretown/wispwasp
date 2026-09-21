"""Run the normal WispWasp behavior suites.

This is the canonical list used by the build, CI, and documentation checks.
Hardware/live-network exercises stay separate because they are not reliable
release gates on every machine.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

NORMAL_TEST_SUITES = (
    "test_wav.py",
    "test_core2.py",
    "test_engine.py",
    "test_server.py",
    "test_layouts.py",
    "test_setup.py",
    "test_install.py",
    "test_clear.py",
    "test_catalog.py",
    "test_models.py",
    "test_recovery.py",
    "test_activation.py",
    "test_options.py",
    "test_sync.py",
    "test_favourites.py",
    "test_confirm.py",
    "test_customize.py",
    "test_docs.py",
)

_SUMMARY = re.compile(r"^\s*(?:\d+/\d+ passed|all checks passed)", re.I)


def _summary(output: str) -> str:
    for line in reversed(output.splitlines()):
        if _SUMMARY.search(line):
            return line.strip()
    return "passed"


def main() -> int:
    missing = [name for name in NORMAL_TEST_SUITES if not (ROOT / name).is_file()]
    if missing:
        print("Missing normal test suite(s):", file=sys.stderr)
        for name in missing:
            print(f"  - {name}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="wispwasp-tests-") as temp_root:
        temp_root = Path(temp_root)
        for name in NORMAL_TEST_SUITES:
            env = os.environ.copy()
            # Tests are separate processes but used to share persisted
            # settings and prompt/style state. That made results depend on
            # suite order and on whatever the developer last did in the app.
            env["WISPWASP_DATA_DIR"] = str(temp_root / Path(name).stem)

            proc = subprocess.run(
                [sys.executable, name],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
            )
            if proc.returncode:
                print(f"\n=== {name} FAILED ===", file=sys.stderr)
                print(proc.stdout, file=sys.stderr, end="")
                return proc.returncode

            print(f"  {name:<24} {_summary(proc.stdout)}")

    print(f"\n{len(NORMAL_TEST_SUITES)} normal suites passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
