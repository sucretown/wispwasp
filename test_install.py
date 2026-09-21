"""
Exercises the full install flow against a local server.

A real 7z archive shaped like the ComfyUI portable build is built, served,
downloaded, extracted and detected - the same code path the 1.8 GB
download takes, without moving 1.8 GB.
"""
import http.server
import shutil
import threading
from pathlib import Path


from avcore import setup as avsetup
from avcore.config import Settings

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  {'PASS' if cond else '*** FAIL ***':<14} {name}"
          + (f"  [{detail}]" if detail else ""))


tmp = Path("_installtest").resolve()
shutil.rmtree(tmp, ignore_errors=True)
(tmp / "srv").mkdir(parents=True)

print("=== building a stand-in portable archive ===")
stage = tmp / "stage" / "ComfyUI_windows_portable"
(stage / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True)
(stage / "python_embeded").mkdir(parents=True)
(stage / "ComfyUI" / "main.py").write_text("# comfy")
(stage / "python_embeded" / "python.exe").write_bytes(b"MZ fake")
for i in range(60):
    (stage / "ComfyUI" / f"file_{i}.txt").write_text("x" * 500)

archive = (tmp / "srv" / "portable.7z").resolve()
# 7zr stores exactly the path it is given, so this runs from the staging
# folder and adds a bare name - otherwise the archive root would be
# "_installtest\stage\ComfyUI_windows_portable" and nothing would be found
# where it is expected after extraction.
import subprocess
subprocess.run([str(avsetup.seven_zip_exe()), "a", str(archive),
                "ComfyUI_windows_portable"],
               cwd=str(stage.parent), capture_output=True, check=True)
check("archive built", archive.exists(),
      f"{archive.stat().st_size // 1024} KB")

MODEL_BYTES = b"SAFETENSORS" * 90_000
(tmp / "srv" / "model.safetensors").write_bytes(MODEL_BYTES)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(tmp / "srv"), **kw)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 8491), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

print("\n=== running the installer end to end ===")
# Point the installer at the local server instead of GitHub and Hugging
# Face. Everything else is the real code path.
avsetup.comfy_asset_url = lambda: (
    "http://127.0.0.1:8491/portable.7z", archive.stat().st_size)
# Setup fetches whichever tier is chosen, so the tier itself is
# redirected at the local test server rather than the old module-level
# constants, which the installer no longer reads.
avsetup.TIERS[avsetup.DEFAULT_TIER] = dict(
    avsetup.TIERS[avsetup.DEFAULT_TIER],
    url="http://127.0.0.1:8491/model.safetensors",
    name="model.safetensors",
    min_bytes=1,
)
# The stub model is tiny, so the bar for 'this is a real
# checkpoint' has to come down with it.
avsetup.PLAUSIBLE_MODEL_BYTES = 1
avsetup.REQUIRED_FREE_BYTES = 1024 * 1024      # so the space check passes

root = (tmp / "install").resolve()
root.mkdir(parents=True)

steps = []
progress = []
s = Settings.load(path=tmp / "settings.json")
s.set("comfyui.path", str(root))

inst = avsetup.Installer(
    s,
    on_step=lambda k, t: steps.append(k),
    on_progress=lambda **kw: progress.append(kw),
)
report = inst.run(root=root)

check("ComfyUI step ran", "comfy" in steps)
check("extraction step ran", "extract" in steps)
check("model step ran", "model" in steps)
check("finished", steps[-1] == "done", " -> ".join(steps))

found = avsetup.find_comfy(root)
check("install is detected afterwards", found is not None)
check("it looks portable", found and found["portable"] is True)
check("embedded python is present",
      found and found["python"].exists())
check("all archive contents extracted",
      len(list((found["base"] / "ComfyUI").glob("file_*.txt"))) == 60)

model = found["models"] / "model.safetensors"
check("model landed in the checkpoints folder", model.exists())
check("model is intact", model.read_bytes() == MODEL_BYTES)
check("report says ready", report["ready"])

print("\n=== settings were written ===")
check("path saved", s.get("comfyui.path") == str(root))
check("autostart enabled", s.get("comfyui.autostart") is True)
check("settings persisted to disk", (tmp / "settings.json").exists())

print("\n=== the archive is cleaned up ===")
check("downloaded .7z removed after extraction",
      not (root / avsetup.COMFY_ASSET).exists()
      and not list(root.glob("*.7z")))

print("\n=== running again is a no-op ===")
steps.clear()
inst.run(root=root)
check("nothing re-downloaded", "comfy" not in steps and "model" not in steps,
      " -> ".join(steps))

print("\n=== progress was reported throughout ===")
check("download progress seen",
      any(p.get("label") == "ComfyUI" for p in progress))
check("unpack progress seen",
      any(p.get("label") == "Unpacking" for p in progress))
check("model progress seen",
      any(p.get("label") == "Image model" for p in progress))

srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("the install flow works")
