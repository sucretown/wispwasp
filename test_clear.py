"""
Checks the overlay page is told to go blank, and that clearing does not
touch the file.

The engine tests cover the state; this covers what the browser actually
receives, since a stale state.json would leave the image on stream.
"""
import json
import shutil
import time
from pathlib import Path

import requests

from avcore.config import Settings
from avcore.engine import Engine
from avcore.server import OverlayServer
from demo_stubs import write_png

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


tmp = Path("_cleartest").resolve()
shutil.rmtree(tmp, ignore_errors=True)
# Tolerates a leftover: Windows will not delete a folder a live
# window still has open, so the cleanup at the end can fail.
tmp.mkdir(parents=True, exist_ok=True)

s = Settings.load()
s.set("paths.overlay_dir", str(tmp))
s.set("server.port", 8493)
eng = Engine(s)
srv = OverlayServer(s, app_dir=Path(".").resolve())
srv.start()
time.sleep(1.2)
base = "http://127.0.0.1:8493"

write_png(tmp / "live_shot.png", 320, 180, (90, 150, 210))
eng.publish_overlay(tmp / "live_shot.png", source="live")
time.sleep(0.3)

state = requests.get(f"{base}/state.json", timeout=10).json()
check("the page is served an image to show", state["image"] == "live_shot.png")
img = requests.get(f"{base}/img/live_shot.png", timeout=10)
check("and can fetch it", img.status_code == 200,
      f"{len(img.content)} bytes")

print("\n=== after clearing ===")
eng.clear_overlay()
time.sleep(0.3)
state = requests.get(f"{base}/state.json", timeout=10).json()
check("the page is told there is nothing to show", state["image"] is None)
check("and that it was deliberate", state.get("cleared") is True)

# The distinction that matters: the page shows nothing, but the file is
# untouched and still reachable.
check("the file is still on disk", (tmp / "live_shot.png").exists())
again = requests.get(f"{base}/img/live_shot.png", timeout=10)
check("and is still served if asked for directly",
      again.status_code == 200, f"HTTP {again.status_code}")

print("\n=== the page itself handles a blank state ===")
page = requests.get(f"{base}/overlay.html", timeout=10).text
check("the page has code for hiding", "function hide()" in page)
check("it reacts to a missing image", "if (!s.image)" in page)
check("bare mode stays silent when cleared", "if (!bare)" in page)
check("no stale reference to the retired script",
      "listener.py" not in page)

eng.shutdown()
srv_thread_alive = srv.is_running()
shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("clearing the overlay works end to end")
