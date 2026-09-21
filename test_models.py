"""
The model catalogue: searching, and downloading without losing work.

Every network call is faked. These tests are about how the code behaves
when a download is interrupted, resumed, refused or truncated - which is
most of what matters for a file measured in gigabytes - and none of that
needs a real server.
"""

import io
import json
import shutil
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QLabel

from avcore.models import (
    CatalogueError, ModelInfo, NeedsAccount, download, installed,
    part_files, search,
)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    mark = "PASS" if ok else "FAIL"
    print(f"  {mark}  {name:<52}{('  [' + detail + ']') if detail else ''}")


tmp = Path("_modeltest").resolve()
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir(parents=True, exist_ok=True)


class FakeResponse(io.BytesIO):
    """Just enough of an HTTP response."""

    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def catalogue_payload(count=3, nsfw=False):
    items = []
    for i in range(count):
        items.append({
            "id": 100 + i,
            "name": f"Model {i}",
            "description": "<p>A <b>useful</b> checkpoint.</p>",
            "nsfw": nsfw,
            "allowCommercialUse": ["Image"],
            "creator": {"username": "someone"},
            "tags": ["photo", "general"],
            "modelVersions": [{
                "baseModel": "SD 1.5",
                "images": [{"url": "https://example/img.png", "nsfw": False}],
                "files": [{
                    "primary": True,
                    "name": f"model_{i}.safetensors",
                    "sizeKB": 2_082_816,
                    "downloadUrl": f"https://example/download/{i}",
                }],
            }],
        })
    return json.dumps({"items": items}).encode("utf-8")


print("=== searching the catalogue ===")

seen = {}


def fake_search(request, timeout=None):
    seen["url"] = request.full_url
    seen["headers"] = dict(request.headers)
    return FakeResponse(catalogue_payload())


found = search(opener=fake_search)
check("it returns models", len(found) == 3, str(len(found)))
check("sizes are converted to bytes",
      found[0].size_bytes == 2_082_816 * 1024)
check("and shown in a form a person can judge",
      found[0].size_label == "2.1 GB", found[0].size_label)
check("html is stripped from descriptions",
      found[0].description == "A useful checkpoint.",
      found[0].description)
check("only checkpoints are asked for", "types=Checkpoint" in seen["url"])
check("the base model is passed through",
      "baseModels=SD+1.5" in seen["url"] or "SD%201.5" in seen["url"],
      seen["url"])

print("\n  adult content is filtered by default:")
check("the request asks for safe entries", "nsfw=false" in seen["url"])

search(include_adult=True, opener=fake_search)
check("and does not when adult content is wanted",
      "nsfw=false" not in seen["url"], seen["url"])


def adult_only(request, timeout=None):
    return FakeResponse(catalogue_payload(nsfw=True))


check("an adult entry is dropped even if the catalogue returns it",
      search(opener=adult_only) == [],
      "the filter is not left to the far end alone")
check("and kept when it was asked for",
      len(search(include_adult=True, opener=adult_only)) == 3)

print("\n  when the catalogue will not answer:")


def refuses(request, timeout=None):
    raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized",
                                 {}, None)


try:
    search(opener=refuses)
    check("a refusal is reported as needing an account", False)
except NeedsAccount as exc:
    check("a refusal is reported as needing an account",
          "API key" in str(exc), "not as a crash")
except Exception as exc:
    check("a refusal is reported as needing an account", False, str(exc))


def breaks(request, timeout=None):
    raise urllib.error.HTTPError(request.full_url, 500, "Server Error",
                                 {}, None)


try:
    search(opener=breaks)
    check("a server fault is reported plainly", False)
except CatalogueError as exc:
    check("a server fault is reported plainly", "500" in str(exc))

print("\n=== downloading ===")

BODY = bytes(range(256)) * 400          # 102,400 bytes
INFO = ModelInfo(
    name="Test Model",
    description="",
    base_model="SD 1.5",
    nsfw=False,
    file_name="test.safetensors",
    size_bytes=len(BODY),
    download_url="https://example/download/0",
)


def whole_file(request, timeout=None):
    return FakeResponse(BODY, 200,
                        {"Content-Length": str(len(BODY))})


into = tmp / "checkpoints"
path = download(INFO, into, opener=whole_file)
check("the file lands in the folder", path.exists())
check("with the right name", path.name == "test.safetensors")
check("and the right contents", path.read_bytes() == BODY)
check("no part file is left behind", not part_files(into))

print("\n  an interrupted download keeps what it got:")
shutil.rmtree(into, ignore_errors=True)
stopped_after = {"count": 0}


def stop_soon():
    stopped_after["count"] += 1
    return stopped_after["count"] > 2


def slow(request, timeout=None):
    return FakeResponse(BODY, 200, {"Content-Length": str(len(BODY))})


outcome = download(INFO, into, should_stop=stop_soon, opener=slow,
                   chunk=1024)
check("it reports that it did not finish", outcome is None)
parts = part_files(into)
check("a part file is kept", len(parts) == 1, str(len(parts)))
partial = parts[0].stat().st_size if parts else 0
check("holding what had arrived", 0 < partial < len(BODY), f"{partial} bytes")
check("and no finished file is pretended",
      not (into / "test.safetensors").exists(),
      "a truncated checkpoint that looks whole is the worst outcome")

print("\n  resuming asks only for the rest:")
asked = {}


def resume(request, timeout=None):
    asked["range"] = request.headers.get("Range")
    start = int(request.headers.get("Range").split("=")[1].split("-")[0])
    rest = BODY[start:]
    return FakeResponse(rest, 206, {"Content-Length": str(len(rest))})


path = download(INFO, into, opener=resume)
check("a Range header is sent", asked.get("range") is not None,
      str(asked.get("range")))
check("it starts from what was already there",
      asked["range"] == f"bytes={partial}-")
check("the finished file is complete", path.read_bytes() == BODY)
check("and the part file is gone", not part_files(into))

print("\n  a server that ignores Range does not corrupt the file:")
shutil.rmtree(into, ignore_errors=True)
into.mkdir(parents=True)
(into / "test.safetensors.part").write_bytes(BODY[:5000])


def ignores_range(request, timeout=None):
    # Answers 200 with the whole file despite being asked for part.
    return FakeResponse(BODY, 200, {"Content-Length": str(len(BODY))})


path = download(INFO, into, opener=ignores_range)
check("the file is still correct", path.read_bytes() == BODY,
      "appending to the part file would have doubled the start")

print("\n  a download that stops early is not passed off as finished:")
shutil.rmtree(into, ignore_errors=True)


def truncated(request, timeout=None):
    return FakeResponse(BODY[:1000], 200,
                        {"Content-Length": str(len(BODY))})


try:
    download(INFO, into, opener=truncated)
    check("it raises rather than returning a short file", False)
except CatalogueError as exc:
    check("it raises rather than returning a short file",
          "carry on" in str(exc), "and says the progress is kept")
check("no finished file was written",
      not (into / "test.safetensors").exists())

print("\n  a model that needs an account says so:")
shutil.rmtree(into, ignore_errors=True)


def needs_key(request, timeout=None):
    raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)


try:
    download(INFO, into, opener=needs_key)
    check("it explains rather than failing obscurely", False)
except NeedsAccount as exc:
    check("it explains rather than failing obscurely",
          "API key" in str(exc), "and names the model")

print("\n  an API key is attached when there is one:")
keys = {}


def capture_key(request, timeout=None):
    keys["auth"] = request.headers.get("Authorization")
    return FakeResponse(catalogue_payload())


search(api_key="secret-key", opener=capture_key)
check("as a bearer token", keys.get("auth") == "Bearer secret-key",
      str(keys.get("auth")))

search(opener=capture_key)
check("and not when there is not", keys.get("auth") is None)

print("\n  what is already installed:")
shutil.rmtree(into, ignore_errors=True)
into.mkdir(parents=True)
(into / "one.safetensors").write_bytes(b"x")
(into / "two.ckpt").write_bytes(b"x")
(into / "notes.txt").write_text("ignore me")
(into / "half.safetensors.part").write_bytes(b"x")
names = [p.name for p in installed(into)]
check("checkpoints are listed", names == ["one.safetensors", "two.ckpt"],
      str(names))
check("other files are not", "notes.txt" not in names)
check("part files are not offered as models",
      "half.safetensors.part" not in names)
check("but can be found for tidying up", len(part_files(into)) == 1)

shutil.rmtree(tmp, ignore_errors=True)

print("\n=== the browse window ===")
# The catalogue itself is stubbed. What is being checked here is the
# window's behaviour - what it lists, what it enables, where it saves -
# not that Civitai answers.
from PySide6.QtWidgets import QApplication, QPushButton

import avgui.model_browser as browser_module
from avgui import theme
from avgui.model_browser import ConfirmDownload, ModelBrowser

app = QApplication.instance() or QApplication([])
theme.apply_to(app, None)

asked = {}


def fake_search(query="", base_model="SD 1.5", include_adult=False,
                limit=20, kind="Checkpoint", api_key=None, opener=None):
    asked["query"] = query
    asked["base_model"] = base_model
    asked["include_adult"] = include_adult
    # Recorded so the LoRA browser can be shown to ask for LoRAs rather
    # than merely to put them somewhere else.
    asked["kind"] = kind
    return [
        ModelInfo(name="Alpha", description="A model.", base_model="SD 1.5",
                  nsfw=False, file_name="alpha.safetensors",
                  size_bytes=2_147_483_648,
                  download_url="https://example/a", creator="someone",
                  licence="commercial use: Image"),
        ModelInfo(name="Beta", description="", base_model="SD 1.5",
                  nsfw=True, file_name="beta.safetensors",
                  size_bytes=4_294_967_296,
                  download_url="https://example/b"),
    ]


browser_module.search = fake_search

from avcore.config import Settings

panel_tmp = tmp / "browser"
panel_tmp.mkdir(parents=True, exist_ok=True)
sb = Settings.load(path=panel_tmp / "s.json")
sb.set("comfyui.path", str(panel_tmp / "ComfyUI"))
sb.save()

window = ModelBrowser(sb)


def settle(seconds=3.0):
    import time as _time
    end = _time.time() + seconds
    while _time.time() < end:
        app.processEvents()
        _time.sleep(0.01)
        worker = window._search
        if worker is not None and not worker.isRunning():
            app.processEvents()
            break


settle()
check("it lists what the catalogue returned", window.list.count() == 2,
      str(window.list.count()))
check("the search runs off the interface thread",
      isinstance(window._search, browser_module.SearchWorker),
      "a blocking call here froze the whole window once already")

print("\n  the download button waits for a choice:")
check("nothing selected, nothing to download", not window.get.isEnabled())
window.list.setCurrentRow(0)
app.processEvents()
check("choosing one offers it", window.get.isEnabled())

print("\n  it saves where ComfyUI looks:")
folder = window.checkpoints_folder()
check("under the configured ComfyUI",
      folder == panel_tmp / "ComfyUI" / "models" / "checkpoints",
      str(folder))
sb.set("comfyui.path", "")
check("and falls back to the default install",
      window.checkpoints_folder().name == "checkpoints",
      str(window.checkpoints_folder()))
sb.set("comfyui.path", str(panel_tmp / "ComfyUI"))

print("\n  the adult toggle changes the request, not the model:")
window.adult.setChecked(True)
settle()
check("it asks the catalogue for them", asked.get("include_adult") is True)
window.adult.setChecked(False)
settle()
check("and stops asking when unticked",
      asked.get("include_adult") is False)
check("the wording does not promise a restraint",
      "does not restrain" in window.adult.toolTip(),
      window.adult.toolTip()[:48])

print("\n  searching is not styled as a destructive action:")
check("the search button takes the ordinary style",
      window.go.objectName() == "",
      "the primary colour is for actions that change something")

print("\n  the confirmation states what it will cost:")
info = fake_search()[0]
dialog = ConfirmDownload(info, folder)
labels = " ".join(l.text() for l in dialog.findChildren(QLabel))
buttons = {b.objectName(): b for b in dialog.findChildren(QPushButton)}
# Same file, decimal units.
check("the size, in the title of the button", "2.1 GB" in labels)
check("where it is going", "checkpoints" in labels)
check("the licence terms", "commercial use" in labels)
check("that it can be resumed", "picked up again" in labels)
check("and refusing is the default",
      buttons["denyButton"].isDefault(),
      "nothing measured in gigabytes starts on a stray Enter")
dialog.reject()

print("\n=== managing what is installed ===")
from avgui.model_browser import ConfirmRemove

store = panel_tmp / "ComfyUI" / "models" / "checkpoints"
store.mkdir(parents=True, exist_ok=True)
(store / "alpha.safetensors").write_bytes(b"x" * 3_000_000)
(store / "beta.safetensors").write_bytes(b"x" * 5_000_000)
(store / "gamma.ckpt").write_bytes(b"x" * 1_000_000)
(store / "leftover.safetensors.part").write_bytes(b"x" * 2_000_000)
sb.set("comfyui.checkpoint", "beta.safetensors")

window.tabs.setCurrentIndex(1)
app.processEvents()

check("it lists what is on disk", window.have.count() == 3,
      str(window.have.count()))
check("with the total disk used", "9 MB" in window.disk.text(),
      window.disk.text())
check("sizes read sensibly below a gigabyte",
      "MB" in window.have.item(0).text(),
      "a list of 0.00 GB entries says nothing")
rows = [window.have.item(i).text() for i in range(window.have.count())]
check("the model in use is marked",
      any("in use" in row for row in rows),
      "removing it changes what gets generated")
check("unfinished downloads are counted separately",
      "unfinished" in window.leftovers.text(), window.leftovers.text())
check("and can be cleared", not window.tidy.isHidden())
check("download is hidden on this tab", not window.get.isVisible(),
      "a greyed-out button invites the question of why")

print("\n  removing waits for a choice:")
check("nothing picked, nothing to remove", not window.remove.isEnabled())
window.have.setCurrentRow(0)
app.processEvents()
check("picking one offers it", window.remove.isEnabled())

print("\n  the confirmation says what it costs:")
dialog = ConfirmRemove(store / "beta.safetensors", "5 MB", True)
words = " ".join(l.text() for l in dialog.findChildren(QLabel))
check("how much it frees", "5 MB" in words)
check("that it cannot be undone", "cannot be recovered" in words)
check("and that this one is in use", "currently selected" in words)
buttons = {b.objectName(): b for b in dialog.findChildren(QPushButton)}
check("keeping it is the default", buttons["denyButton"].isDefault())
dialog.reject()

print("\n  clearing unfinished downloads:")
window._tidy()
app.processEvents()
check("the part file is gone", not list(store.glob("*.part")))
check("it says how much was freed", "2 MB" in window.manage_note.text(),
      window.manage_note.text())
check("the models themselves are untouched", window.have.count() == 3)

print("\n  each tab keeps its own message:")
# A search finishing in the background used to wipe out the message
# about what had just been deleted.
window.find_note.setText("a search result")
window.manage_note.setText("a deletion result")
window._show(fake_search())
app.processEvents()
check("the find tab's line updated",
      "models" in window.find_note.text(), window.find_note.text())
check("the installed tab's line survived",
      window.manage_note.text() == "a deletion result",
      window.manage_note.text())

window.close()
app.processEvents()

print("\n=== progress survives a file bigger than 2 GB ===")
# Qt's int is 32 bits. A worker declaring Signal(int, int) wrapped any
# size over 2,147,483,647: a 4.27GB download arrived as -29,870,600 and
# the bar read "0.14 of -0.03 GB". Worse, the larger models wrapped to
# believable numbers - 9.6GB came through as 5.3GB, which nobody would
# think to report.
from PySide6.QtCore import QObject, Signal

from avgui.model_browser import DownloadWorker
from avgui.setup_panel import ModelFetch

LIMIT = 2 ** 31 - 1


def _carries(worker, value):
    """What a size looks like after a trip through the signal."""
    seen = []
    worker.progress.connect(lambda done, total: seen.append(total))
    worker.progress.emit(0, value)
    app.processEvents()
    return seen[-1] if seen else None


_dummy = ModelInfo(name="x", description="", base_model="", nsfw=False,
                   file_name="x", size_bytes=1, download_url="")
for name, worker in (("the model browser",
                      DownloadWorker(_dummy, tmp, None)),
                     ("the setup fetcher",
                      ModelFetch(Settings.load(path=tmp / "wrap.json")))):
    for label, size in (("a 4.27 GB model", 4_265_096_696),
                        ("the 9.6 GB video model", 9_559_625_980),
                        ("a 2 GB model", 2_000_000_000)):
        carried = _carries(worker, size)
        check(f"{name} carries {label} intact",
              carried == size,
              f"{carried:,} instead of {size:,}"
              if carried != size else f"{size:,}")

check("the wrap this replaces produced a negative",
      4_265_096_696 - 2 ** 32 < 0,
      "which is what showed as -0.03 GB")
check("and sizes under the limit were never affected",
      2_000_000_000 < LIMIT,
      "which is why small models looked fine and nobody noticed")

print("\n=== LoRAs ===")
# The same browser, pointed at a different word and a different folder.
# One dialog rather than two, because a second copy would be a second
# place for a bug to live.
from avcore.setup import checkpoints_dir, loras_dir
from avgui.model_browser import ModelBrowser

_lora_view = ModelBrowser(sb, kind="LORA")
_ckpt_view = ModelBrowser(sb, kind="Checkpoint")

check("LoRAs land in the loras folder",
      _lora_view.target_folder().name == "loras",
      str(_lora_view.target_folder().name))
check("checkpoints still land in checkpoints",
      _ckpt_view.target_folder().name == "checkpoints")
check("which are different folders",
      loras_dir(sb) != checkpoints_dir(sb),
      "ComfyUI looks in its own place for each, and a LoRA among the "
      "checkpoints simply never appears")
check("and they sit side by side",
      loras_dir(sb).parent == checkpoints_dir(sb).parent)

check("the window says which it is showing",
      _lora_view.windowTitle() == "LoRAs"
      and _ckpt_view.windowTitle() == "Models",
      f"{_lora_view.windowTitle()} / {_ckpt_view.windowTitle()}")
check("so does the tab",
      "LoRA" in _lora_view.tabs.tabText(0)
      and "model" in _ckpt_view.tabs.tabText(0))

_lora_view.close()
_ckpt_view.close()

print("\n=== finding the cut-outs again ===")
# Read from the PNG header rather than by decoding: the gallery asks
# this of every file whenever a filter changes.
from avcore.catalog import has_transparency
from avgui.filters import Filters
from PySide6.QtGui import QColor, QImage

_alpha = tmp / "cut.png"
_clear = QImage(32, 32, QImage.Format_ARGB32)
_clear.fill(QColor(0, 0, 0, 0))
_clear.save(str(_alpha), "PNG")

_solid = tmp / "flat.png"
_opaque = QImage(32, 32, QImage.Format_RGB32)
_opaque.fill(QColor(20, 30, 40))
_opaque.save(str(_solid), "PNG")

check("a cut-out is recognised", has_transparency(_alpha))
check("an ordinary picture is not", not has_transparency(_solid))
check("and something that is not there is not either",
      not has_transparency(tmp / "missing.png"),
      "a missing file must not raise mid-filter")

_kinds = Filters()
_kinds.kind = "transparent"
check("the filter keeps cut-outs",
      _kinds.matches(_alpha, {}, False, False))
check("and drops the rest",
      not _kinds.matches(_solid, {}, False, False))

_kinds.kind = "any"
check("with Any type, both are shown",
      _kinds.matches(_alpha, {}, False, False)
      and _kinds.matches(_solid, {}, False, False))

print("\n=== applying LoRAs ===")
# Chained between the checkpoint and everything that reads from it. Both
# the model and the text encoder come through the chain: one that
# changed the model but not the CLIP gives a picture that half-remembers
# the style it was asked for.
from avcore.images import ComfyBackend
from avgui.lora_chooser import active_count, merged, stored

_loras = loras_dir(sb)
_loras.mkdir(parents=True, exist_ok=True)
for _name in ("one.safetensors", "two.safetensors"):
    (_loras / _name).write_bytes(b"0" * 2048)

_backend = ComfyBackend(sb)
_backend.set_checkpoint("pinned.safetensors")

print("\n  which ones count as chosen:")
sb.set("comfyui.loras", [
    {"name": "one.safetensors", "strength": 0.8, "on": True},
    {"name": "two.safetensors", "strength": 1.0, "on": False},
    {"name": "gone.safetensors", "strength": 1.0, "on": True},
])
_chosen = _backend.chosen_loras()
check("a switched-on one is used",
      ("one.safetensors", 0.8) in _chosen)
check("a switched-off one is not",
      not any(n == "two.safetensors" for n, _s in _chosen))
check("and one that is no longer on disk is skipped",
      not any(n == "gone.safetensors" for n, _s in _chosen),
      "passing it to ComfyUI would fail the whole job over a file "
      "somebody deleted months ago")

sb.set("comfyui.loras", [
    {"name": "one.safetensors", "strength": 99.0, "on": True}])
check("a silly strength is brought back to something sane",
      _backend.chosen_loras()[0][1] <= 4.0,
      str(_backend.chosen_loras()[0][1]))

sb.set("comfyui.loras", "not a list")
check("nonsense in the setting does not raise",
      _backend.chosen_loras() == [],
      "a hand-edited settings file should not stop the app generating")

print("\n  how they are chained:")
sb.set("comfyui.loras", [
    {"name": "one.safetensors", "strength": 0.8, "on": True},
    {"name": "two.safetensors", "strength": 0.5, "on": True},
])
_graph = _backend._workflow("a cottage", 768, 768, 7)
_chain = [key for key, node in _graph.items()
          if node["class_type"] == "LoraLoader"]
check("one loader per LoRA", len(_chain) == 2, str(_chain))
check("the first hangs off the checkpoint",
      _graph["20"]["inputs"]["model"] == ["1", 0])
check("the second hangs off the first",
      _graph["21"]["inputs"]["model"] == ["20", 0],
      "each is applied over the last, so order matters")
check("the sampler reads the end of the chain",
      _graph["5"]["inputs"]["model"] == ["21", 0])
check("and so does the text encoder",
      _graph["2"]["inputs"]["clip"] == ["21", 1],
      "a LoRA that moves the model but not the CLIP half-works")
check("strengths are carried through",
      _graph["20"]["inputs"]["strength_model"] == 0.8
      and _graph["21"]["inputs"]["strength_model"] == 0.5)

sb.set("comfyui.loras", [])
_plain = _backend._workflow("a cottage", 768, 768, 7)
check("with none chosen, nothing is inserted",
      not any(n["class_type"] == "LoraLoader" for n in _plain.values()))
check("and the graph is wired as it always was",
      _plain["5"]["inputs"]["model"] == ["1", 0]
      and _plain["2"]["inputs"]["clip"] == ["1", 1])

print("\n  what the chooser shows:")
sb.set("comfyui.loras", [
    {"name": "one.safetensors", "strength": 0.7, "on": True}])
_rows = merged(sb)
check("every installed LoRA is listed", len(_rows) == 2, str(len(_rows)))
check("the chosen one keeps its strength",
      next(r for r in _rows if r["name"] == "one.safetensors")["strength"]
      == 0.7)
check("one never seen before starts off",
      not next(r for r in _rows
               if r["name"] == "two.safetensors")["on"],
      "installing a LoRA should not silently change every picture")
check("the count is what the strip shows", active_count(sb) == 1)

sb.set("comfyui.loras", [])

print("\n=== which LoRAs can actually be used ===")
# Two ways for a LoRA to do nothing, and both are silent: one built on
# another architecture, and one for the other half of Stable Diffusion.
# ComfyUI loads either happily, matches none of the weights, and the
# picture comes out unchanged with no error to explain why.
from avcore.setup import _family_from_header

check("an SDXL LoRA is recognised by its two text encoders",
      _family_from_header({
          "lora_te1_text_model_encoder_layers_0_mlp_fc1.alpha": {},
          "lora_te2_text_model_encoder_layers_0_mlp_fc1.alpha": {},
          "lora_unet_down_blocks_0.alpha": {},
      }) == "sdxl")

check("an SD 1.5 LoRA by its one",
      _family_from_header({
          "lora_te_text_model_encoder_layers_0_mlp_fc1.alpha": {},
          "lora_unet_down_blocks_0.alpha": {},
      }) == "sd15")

check("a transformer LoRA is neither",
      _family_from_header({
          "diffusion_model.transformer_blocks.0.attn.add_k_proj.lora_A.weight": {},
      }) is None,
      "Flux, Qwen, Wan and friends have no U-Net at all")

print("\n  the weights outrank the label:")
# A real file on this machine declared sd_1.5 while carrying 1680
# transformer-block tensors, the same shape as the Qwen LoRA beside it.
check("a mislabelled file is judged by what is in it",
      _family_from_header({
          "__metadata__": {"ss_base_model_version": "sd_1.5"},
          "diffusion_model.transformer_blocks.0.attn.add_k_proj.lora_A.weight": {},
      }) is None,
      "trusting the label would list it as usable, which is the exact "
      "confusion this prevents")

check("but the label is used when the keys say nothing",
      _family_from_header({
          "__metadata__": {"ss_base_model_version": "sdxl_base_v1-0"},
          "something_unfamiliar": {},
      }) == "sdxl",
      "better than nothing when it is all there is")

check("and an empty file is simply unknown",
      _family_from_header({}) is None)

print("\n  what the chooser says:")
from avgui.lora_chooser import verdict

_note = verdict(sb, "nothing-here.safetensors")
check("a file that cannot be read is called incompatible",
      _note[0] is False
      and "Stable Diffusion" in _note[1],
      str(_note))

print("\n=== no route to a model bypasses safe mode ===")
# The pickers were filtered, but "first found" never went through one:
# it asked ComfyUI for everything and took the first, which on a real
# machine was the adult model the lists were hiding.
from avcore.images import ComfyBackend as _Backend
from avcore.setup import looks_adult

_names = ["aModelNSFW_v1.safetensors", "ordinary_v2.safetensors",
          "another_v3.safetensors"]


class _Pretend(_Backend):
    """Stands in for ComfyUI, with an adult model listed first."""

    def list_checkpoints(self):
        return list(_names)


sb.set("comfyui.checkpoint", "")
sb.set("safety.safe_mode", False)
_loose = _Pretend(sb).checkpoint()
check("off, first found takes ComfyUI's first",
      _loose == "aModelNSFW_v1.safetensors", _loose)

sb.set("safety.safe_mode", True)
_tight = _Pretend(sb).checkpoint()
check("on, it skips to the first allowed one",
      _tight == "ordinary_v2.safetensors", _tight)
check("which is not an adult model", not looks_adult(_tight))

print("\n  a named adult model is refused too:")
sb.set("comfyui.checkpoint", "aModelNSFW_v1.safetensors")
_named = _Pretend(sb).checkpoint()
check("asking for one by name does not get it",
      not looks_adult(_named), _named,)
check("something usable is used instead",
      _named in _names)

print("\n  the cache notices the tick:")
_one = _Pretend(sb)
_one.s.set("comfyui.checkpoint", "")
_one.s.set("safety.safe_mode", False)
_before = _one.checkpoint()
_one.s.set("safety.safe_mode", True)
_after = _one.checkpoint()
check("the same backend re-resolves rather than remembering",
      _before != _after and not looks_adult(_after),
      f"{_before} then {_after}")

print("\n  when nothing is left:")


class _AllAdult(_Backend):
    def list_checkpoints(self):
        return ["oneNSFW.safetensors", "twoHentai.safetensors"]


sb.set("comfyui.checkpoint", "")
try:
    _AllAdult(sb).checkpoint()
    _said = ""
except Exception as exc:
    _said = str(exc)
check("it says so rather than quietly using one",
      "safe mode" in _said.lower(), _said[:70] or "no error raised")

sb.set("safety.safe_mode", False)
sb.set("comfyui.checkpoint", "")

bad = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad)}/{len(results)} passed")
if bad:
    print("FAILURES:")
    for n in bad:
        print(f"  - {n}")
    raise SystemExit(1)
print("the model catalogue works")
