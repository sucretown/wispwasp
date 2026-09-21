"""
Recovery when ComfyUI goes away mid-session.

The bug this covers: the engine used to check for ComfyUI once, at
startup. If it happened to be running then, the check was ticked off and
never repeated - so when that instance later closed, every render failed
forever and the only way back was restarting the app.
"""
import time
from pathlib import Path

from avcore.config import Settings
from avcore.engine import Engine

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


class FakeComfy:
    """A backend whose availability can be switched at will."""
    extension = "png"

    def __init__(self):
        self.up = True
        self.renders = 0

    def is_up(self, timeout=4):
        return self.up

    def generate(self, prompt, dest, seed=None, cancel=None):
        if not self.up:
            from avcore.images import GenerationError
            raise GenerationError("ComfyUI is not running")
        self.renders += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG fake")
        return dest

    def interrupt(self):
        pass


class FakeLauncher:
    """Stands in for the real launcher; records what was asked of it."""

    def __init__(self, backend):
        self.backend = backend
        self.starts = 0
        self.error = ""
        self.booting = False

    def is_starting(self):
        return self.booting

    def wait_until_up(self, seconds, on_progress=None):
        return self.backend.up

    def start(self, wait=240, on_progress=None):
        self.starts += 1
        # A real start takes time; this one succeeds immediately.
        self.backend.up = True
        return True

    def stop(self):
        pass


tmp = Path("_recovertest").resolve()
import shutil
shutil.rmtree(tmp, ignore_errors=True)

s = Settings.load()
s.set("paths.overlay_dir", str(tmp / "out"))
s.set("paths.manual_dir", str(tmp / "out"))
s.set("comfyui.autostart", True)
s.set("image.backend", "comfyui")

eng = Engine(s)
backend = FakeComfy()
eng._backend = backend
launcher = FakeLauncher(backend)
eng._launcher = launcher
eng._recorder = object()
eng._transcriber = object()
eng.start()

print("=== it works while ComfyUI is up ===")
eng.queue_manual("a harbour at dusk")
deadline = time.time() + 15
while time.time() < deadline and backend.renders < 1:
    time.sleep(0.1)
check("the first image generated", backend.renders == 1)
check("nothing needed starting", launcher.starts == 0)

print("\n=== ComfyUI goes away ===")
backend.up = False
eng._comfy_attempt = 0        # allow an immediate retry
eng.queue_manual("a copper kettle")
deadline = time.time() + 20
while time.time() < deadline and backend.renders < 2:
    time.sleep(0.1)

# This is the whole point: it noticed, restarted it, and carried on.
check("it tried to start ComfyUI again", launcher.starts >= 1,
      f"{launcher.starts} attempt(s)")
check("and the image generated after the restart", backend.renders == 2)
check("no error is left showing once it recovered",
      not eng.snapshot().get("error"), eng.snapshot().get("error", "")[:40])

print("\n=== it does not spawn a second copy while one is booting ===")
backend.up = False
launcher.booting = True
before = launcher.starts
eng._comfy_attempt = 0
eng.queue_manual("a stone bridge")
time.sleep(3)
check("it waited rather than starting another", launcher.starts == before,
      f"{launcher.starts} total")
launcher.booting = False
backend.up = True

print("\n=== repeated failures are spaced out ===")


class FailingLauncher(FakeLauncher):
    def start(self, wait=240, on_progress=None):
        self.starts += 1
        self.error = "ComfyUI could not be started."
        return False


eng2 = Engine(s)
b2 = FakeComfy()
b2.up = False
eng2._backend = b2
failing = FailingLauncher(b2)
eng2._launcher = failing
eng2._recorder = object()
eng2._transcriber = object()
eng2.start()

# Several prompts in quick succession must not mean several launches: a
# real start takes the better part of a minute.
for i in range(4):
    eng2.queue_manual(f"prompt {i}")
time.sleep(6)
check("four failed prompts caused at most one launch attempt",
      failing.starts <= 1, f"{failing.starts} attempt(s)")
check("the message says what is wrong",
      "comfyui" in (eng2.snapshot().get("error") or "").lower(),
      (eng2.snapshot().get("error") or "")[:52])

eng.shutdown()
eng2.shutdown()
shutil.rmtree(tmp, ignore_errors=True)

print("\n=== stopping kills the whole tree, not just the parent ===")
# ComfyUI relaunches itself, so the process we spawn is the parent of the
# one actually serving. Killing only our own handle left the child alive,
# holding the port and the GPU.
import subprocess
import sys as _sys

from avcore.comfy_launcher import ComfyLauncher

child_code = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
    "time.sleep(120)"
)
parent = subprocess.Popen([_sys.executable, "-c", child_code])
time.sleep(2.5)

try:
    import psutil
    tree = psutil.Process(parent.pid)
    kids = tree.children(recursive=True)
    check("the test process really did spawn a child", len(kids) >= 1,
          f"{len(kids)} child(ren)")
    kid_pids = [k.pid for k in kids]

    launcher = ComfyLauncher(s)
    launcher.process = parent
    launcher.stop()
    time.sleep(3)

    check("the parent was stopped",
          parent.poll() is not None or not psutil.pid_exists(parent.pid))
    still = [p for p in kid_pids if psutil.pid_exists(p)]
    check("and so were its children", not still,
          f"{len(still)} left behind")
except ImportError:
    check("psutil available for the tree kill", False)
finally:
    try:
        parent.kill()
    except Exception:
        pass



print("\n=== ComfyUI is started at launch, not at the first render ===")
# The point: the model list in Settings can only be read from a running
# ComfyUI, so waiting until the first generation meant there was nothing
# to choose from until you had already generated something.
from avcore.engine import Engine as _Engine


class WarmBackend:
    """A backend that is down until something starts it."""
    extension = "png"

    def __init__(self, up=False):
        self.up = up
        self.checks = 0

    def is_up(self, timeout=4):
        self.checks += 1
        return self.up

    def interrupt(self):
        pass


class WarmLauncher:
    def __init__(self, backend, succeed=True):
        self.backend = backend
        self.succeed = succeed
        self.starts = 0
        self.error = ""

    def is_starting(self):
        return False

    def wait_until_up(self, seconds, on_progress=None):
        return self.backend.up

    def start(self, wait=240, on_progress=None):
        self.starts += 1
        if on_progress:
            on_progress("Starting ComfyUI...")
        self.backend.up = self.succeed
        if not self.succeed:
            self.error = "ComfyUI could not be started."
        return self.succeed

    def stop(self):
        pass


warm = Path("_warmtest").resolve()
shutil.rmtree(warm, ignore_errors=True)
warm.mkdir(parents=True, exist_ok=True)
sw = Settings.load(path=warm / "s.json")
sw.set("paths.overlay_dir", str(warm / "out"))
sw.set("comfyui.autostart", True)

engw = _Engine(sw)
backend = WarmBackend(up=False)
engw._backend = backend
launcher = WarmLauncher(backend)
engw._launcher = launcher

check("not ready before starting", not engw.snapshot().get("comfy_ready"))
engw.start()          # nothing queued, nothing listening
deadline = time.time() + 10
while time.time() < deadline and not engw.snapshot().get("comfy_ready"):
    time.sleep(0.1)

check("it started ComfyUI on its own", launcher.starts == 1,
      f"{launcher.starts} start(s)")
check("and reports being ready", engw.snapshot().get("comfy_ready") is True)
check("without anything having been generated",
      engw.snapshot().get("generated", 0) == 0)
engw.shutdown()

print("\n  an already-running ComfyUI is left alone:")
engw2 = _Engine(sw)
running = WarmBackend(up=True)
engw2._backend = running
l2 = WarmLauncher(running)
engw2._launcher = l2
engw2.start()
time.sleep(1.5)
check("it did not start a second copy", l2.starts == 0)
check("but still reports ready", engw2.snapshot().get("comfy_ready") is True)
engw2.shutdown()

print("\n  a failure to start is not fatal:")
engw3 = _Engine(sw)
broken = WarmBackend(up=False)
engw3._backend = broken
l3 = WarmLauncher(broken, succeed=False)
engw3._launcher = l3
engw3.start()
time.sleep(2)
snap = engw3.snapshot()
check("it tried", l3.starts == 1)
check("ready stays false", not snap.get("comfy_ready"))
check("the message explains", "comfyui" in (snap.get("error") or "").lower(),
      (snap.get("error") or "")[:40])
check("the app is still running", engw3._thread.is_alive())
engw3.shutdown()

print("\n  autostart off means it is left alone entirely:")
sw.set("comfyui.autostart", False)
engw4 = _Engine(sw)
b4 = WarmBackend(up=False)
engw4._backend = b4
l4 = WarmLauncher(b4)
engw4._launcher = l4
engw4.start()
time.sleep(1.5)
check("nothing was started", l4.starts == 0)
engw4.shutdown()
sw.set("comfyui.autostart", True)

shutil.rmtree(warm, ignore_errors=True)

bad = [n for n, ok in results if not ok]

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("it recovers when ComfyUI goes away")
