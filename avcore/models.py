"""
Finding and fetching image models.

The app ships with none: one is fetched during setup, and more can be
added later. This module knows how to ask a catalogue what exists and
how to bring a file down; it deliberately knows nothing about widgets,
so it can be tested without a screen and used from anywhere.

Civitai is the catalogue because it answers the questions a person
actually has before downloading several gigabytes - what is it, how big
is it, what is it based on, what may I do with it - and it gives a
direct download URL. HuggingFace was tried first and rejected: its model
listing does not reliably report file sizes, and a browse list that
cannot say how big something is before you commit is not much use.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .version import __version__

CATALOGUE = "https://civitai.com/api/v1/models"
AGENT = f"WispWasp/{__version__} (+model browser)"

# What the app can actually run. Anything else in the catalogue is
# hidden rather than offered and then failing at generation time.
BASE_MODELS = {
    "SD 1.5": "SD 1.5",
    "SDXL": "SDXL 1.0",
}


class CatalogueError(Exception):
    """Something went wrong talking to the catalogue."""


class NeedsAccount(CatalogueError):
    """
    This model is not downloadable without credentials.

    Kept distinct from a general failure because the answer is
    different: nothing is broken, the user needs to paste an API key
    into Settings or pick a different model.
    """


@dataclass
class ModelInfo:
    """One downloadable checkpoint."""

    name: str
    description: str
    base_model: str
    nsfw: bool
    file_name: str
    size_bytes: int
    download_url: str
    creator: str = ""
    licence: str = ""
    model_id: int = 0
    thumbnail: str = ""
    tags: list = field(default_factory=list)

    @property
    def size_label(self):
        """A size a person can judge, not a number of bytes."""
        if not self.size_bytes:
            return "size unknown"
        # Decimal, so this agrees with the catalogue page it came from.
        gb = self.size_bytes / 1_000_000_000
        if gb >= 1:
            return f"{gb:.1f} GB"
        return f"{self.size_bytes / 1_000_000:.0f} MB"


def _strip_html(text):
    """
    Catalogue descriptions are HTML. Only the words are wanted.

    A real parser would be overkill for what is shown in a list row, and
    would drag in a dependency for one field.
    """
    out = []
    depth = 0
    for char in text or "":
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    cleaned = " ".join("".join(out).split())
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        cleaned = cleaned.replace(entity, char)
    return cleaned


def _open(url, api_key=None, headers=None, opener=None):
    """One request, with the key attached if there is one."""
    request = urllib.request.Request(url)
    request.add_header("User-Agent", AGENT)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    return (opener or urllib.request.urlopen)(request, timeout=30)


def _parse(item):
    """
    Turn one catalogue entry into a ModelInfo, or None.

    Entries without a usable file are dropped rather than shown and then
    failing when pressed: a row that cannot be downloaded is worse than
    no row.
    """
    versions = item.get("modelVersions") or []
    if not versions:
        return None
    version = versions[0]
    files = version.get("files") or []
    if not files:
        return None

    primary = next((f for f in files if f.get("primary")), files[0])
    url = primary.get("downloadUrl")
    if not url:
        return None

    # Sizes come as kilobytes, and the app talks in bytes everywhere
    # else.
    size = int((primary.get("sizeKB") or 0) * 1024)

    allow = item.get("allowCommercialUse") or []
    if isinstance(allow, str):
        allow = [allow]
    licence = "commercial use: " + (", ".join(allow) if allow else "no")

    images = version.get("images") or []
    thumbnail = ""
    for image in images:
        # Only a still, and only one the catalogue itself considers
        # safe: the browser shows these before anyone has agreed to
        # anything.
        if image.get("type") in (None, "image") and not image.get("nsfw"):
            thumbnail = image.get("url") or ""
            break

    return ModelInfo(
        name=item.get("name") or "untitled",
        description=_strip_html(item.get("description"))[:400],
        base_model=version.get("baseModel") or "",
        nsfw=bool(item.get("nsfw")),
        file_name=primary.get("name") or "model.safetensors",
        size_bytes=size,
        download_url=url,
        creator=(item.get("creator") or {}).get("username", ""),
        licence=licence,
        model_id=item.get("id") or 0,
        thumbnail=thumbnail,
        tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
    )


def search(query="", base_model="SD 1.5", include_adult=False,
           limit=20, kind="Checkpoint",
           api_key=None, opener=None):
    """
    Ask the catalogue what is available.

    `include_adult` filters the *list*. It does not restrain any model:
    a capable checkpoint can still produce adult images whatever the
    catalogue says about it, and the option is worded in the interface
    to say so rather than implying a guarantee.
    """
    params = {
        "limit": str(max(1, min(int(limit), 100))),
        # Checkpoint or LORA. Civitai calls them the same way; only
        # this word and the folder they land in differ.
        "types": kind,
        "sort": "Highest Rated",
    }
    wanted = BASE_MODELS.get(base_model)
    if wanted:
        params["baseModels"] = wanted
    if query.strip():
        params["query"] = query.strip()
    if not include_adult:
        params["nsfw"] = "false"

    url = f"{CATALOGUE}?{urllib.parse.urlencode(params)}"
    try:
        with _open(url, api_key, opener=opener) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise NeedsAccount(
                "The catalogue refused the request. An API key may be "
                "needed - you can paste one into Settings.") from exc
        raise CatalogueError(
            f"The catalogue answered {exc.code}. Try again shortly."
        ) from exc
    except Exception as exc:
        raise CatalogueError(
            f"Could not reach the catalogue: {exc}") from exc

    found = []
    for item in payload.get("items", []):
        parsed = _parse(item)
        if parsed is None:
            continue
        # The catalogue's own filter is trusted but not relied upon.
        if not include_adult and parsed.nsfw:
            continue
        found.append(parsed)
    return found


def download(info, folder, on_progress=None, should_stop=None,
             api_key=None, opener=None, chunk=1 << 20):
    """
    Fetch a model into `folder`, resuming if a part file is there.

    Written to be interrupted. These files are gigabytes, the download
    can take an hour on a slow line, and someone will close the app or
    lose their connection part way through. So it writes to a `.part`
    file, asks for the rest with a Range header next time, and only
    moves it into place once the whole thing has arrived - an app that
    finds a truncated checkpoint and tries to load it fails in a way
    that is very hard to understand.

    Returns the finished path. Raises NeedsAccount if credentials are
    required, CatalogueError for anything else.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / info.file_name
    if final.exists() and final.stat().st_size > 0:
        return final

    part = folder / (info.file_name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {}
    if have:
        headers["Range"] = f"bytes={have}-"

    try:
        response = _open(info.download_url, api_key, headers, opener)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise NeedsAccount(
                f"{info.name} needs an account to download. Paste a "
                f"Civitai API key into Settings, or choose another "
                f"model.") from exc
        if exc.code == 416:
            # The part file is already the whole thing, or the server
            # disagrees about its length. Start again rather than guess.
            part.unlink(missing_ok=True)
            raise CatalogueError(
                "The part-finished file did not match the server. "
                "It has been discarded - try again.") from exc
        raise CatalogueError(
            f"The download failed with {exc.code}.") from exc
    except Exception as exc:
        raise CatalogueError(f"The download failed: {exc}") from exc

    with response:
        # A server that ignores Range sends 200 and the whole file, so
        # appending would corrupt it.
        resuming = response.status == 206
        if have and not resuming:
            have = 0
            part.unlink(missing_ok=True)

        remaining = response.headers.get("Content-Length")
        total = (have + int(remaining)) if remaining else info.size_bytes

        mode = "ab" if (have and resuming) else "wb"
        written = have
        last_report = 0.0
        with part.open(mode) as out:
            while True:
                if should_stop is not None and should_stop():
                    # Deliberately leaves the part file: stopping is not
                    # the same as cancelling, and the next attempt
                    # carries on from here.
                    return None
                block = response.read(chunk)
                if not block:
                    break
                out.write(block)
                written += len(block)
                now = time.monotonic()
                if on_progress and (now - last_report) > 0.1:
                    on_progress(written, total)
                    last_report = now

    if total and written < total:
        raise CatalogueError(
            "The download ended early. What arrived is kept, so trying "
            "again will carry on from there.")

    os.replace(part, final)
    if on_progress:
        on_progress(written, total or written)
    return final


def installed(folder):
    """Every checkpoint already sitting in `folder`."""
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted(
        path for path in folder.iterdir()
        if path.suffix.lower() in (".safetensors", ".ckpt")
        and not path.name.startswith("."))


def part_files(folder):
    """Half-finished downloads, which a manage screen should offer to
    clear out."""
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted(folder.glob("*.part"))
