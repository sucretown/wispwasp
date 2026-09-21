"""Checks the overlay server actually serves what the page asks for."""
import time
from pathlib import Path

import requests

from avcore.config import Settings
from avcore.engine import Engine
from avcore.server import OverlayServer, port_is_free
from demo_stubs import write_png

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


tmp = Path("_servertest").resolve()
s = Settings.load()
s.set("paths.overlay_dir", str(tmp))
# A port unlikely to collide with a running listener.py.
s.set("server.port", 8477)

eng = Engine(s)
srv = OverlayServer(s, app_dir=Path(".").resolve())
check("chosen port was free", port_is_free(8477))
check("server started", srv.start())
time.sleep(1.5)
base = "http://127.0.0.1:8477"

r = requests.get(f"{base}/overlay.html", timeout=10)
check("serves the overlay page", r.status_code == 200,
      f"{len(r.content)} bytes")
check("page is the real overlay", "state.json" in r.text)

# Before anything has been generated, the page must still get valid JSON
# rather than a 404 that would make it report lost contact.
r = requests.get(f"{base}/state.json", timeout=10)
check("serves state before any image exists", r.status_code == 200)
check("state is valid json", r.json().get("image") is None)

write_png(tmp / "live_test.png", 320, 180, (80, 140, 200))
eng.publish_overlay(tmp / "live_test.png", source="live")
time.sleep(0.3)

r = requests.get(f"{base}/state.json", timeout=10)
payload = r.json()
check("state names the published image",
      payload.get("image") == "live_test.png", str(payload.get("image")))
check("state reports the source", payload.get("source") == "live")

r = requests.get(f"{base}/img/live_test.png", timeout=10)
check("serves the image itself", r.status_code == 200,
      f"{len(r.content)} bytes")
check("image is a real png", r.content[:8] == b"\x89PNG\r\n\x1a\n")

# The page builds image URLs from the state file, so a name that tries to
# climb out of the folder must not be honoured.
r = requests.get(f"{base}/img/../../settings.json", timeout=10)
check("refuses to serve files outside the folder",
      r.status_code in (403, 404), f"HTTP {r.status_code}")

print("\n=== the live URL follows the bound port, not edited settings ===")
s.set("server.port", 8488)
check("changing settings does not invent a new live URL",
      ":8477/" in srv.url(), srv.url())
check("the bound port still answers",
      requests.get(f"{base}/state.json", timeout=10).status_code == 200)
s.set("server.port", 8477)

print("\n=== a busy port is reported, not raised ===")
second = OverlayServer(s, app_dir=Path(".").resolve())
started = second.start()
check("second server declines to start", started is False)
check("and explains why", "in use" in second.error.lower(), second.error)

print("\n=== stopping releases what was bound ===")
srv.stop()
check("server reports stopped", not srv.is_running())
check("the port is reusable", port_is_free(8477))

eng.shutdown()
import shutil
shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("overlay server works")
