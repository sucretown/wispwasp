"""
One place that says which version this is.

Everything else reads it from here: the window, the installer, the build
script and the update check. It used to live only in installer.iss, so
the running app had no idea what it was, and the build script had the
filename typed out separately - two places to change and one of them
easy to forget.
"""

__version__ = "0.1.9.1"

# Where the app looks to find out whether a newer build exists. Filled
# in once the releases are published; an empty value simply means the
# check is not offered.
UPDATE_MANIFEST = (
    "https://github.com/sucretown/wispwasp/releases/latest/download"
    "/latest.json"
)

# The page a person is sent to when there is one. Deliberately a page
# rather than a direct download: they can read what changed, and see
# older builds if they want to go back.
RELEASES_PAGE = "https://github.com/sucretown/wispwasp/releases/latest"

# Where somebody reports a problem. A link rather than anything clever:
# a form inside the app would need somewhere to send to, and an issue
# tracker already has one.
ISSUES_PAGE = "https://github.com/sucretown/wispwasp/issues"

SOURCE_PAGE = "https://github.com/sucretown/wispwasp"

AUTHOR = "Cinnamoroll"

# What changed in this version, in the words a person would use. Kept
# here rather than parsed out of the release notes so that the app can
# say what it is without reaching for the network.
CHANGES = [
    "Choose several pictures in the gallery at once, then favourite, "
    "censor or delete the lot.",
    "Get more models opens again - it had stopped doing anything at "
    "all when the browser learned to show LoRAs.",
    "Leaving the gallery now closes a playing clip, instead of leaving "
    "it over whatever page you moved to.",
]


def as_tuple(text=None):
    """
    A version as numbers, for comparing.

    Anything unparseable sorts as (0,) rather than raising, so a
    malformed value in a manifest cannot stop the app from starting.
    """
    parts = []
    for chunk in (text or __version__).strip().lstrip("v").split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(candidate, current=None):
    """Is `candidate` a later version than what is running?"""
    return as_tuple(candidate) > as_tuple(current or __version__)
