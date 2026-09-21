# WispWasp

Listens to whatever your PC is playing, transcribes it, and generates an
image from the words. The newest image shows on a page you capture in OBS.

Previously called AudioVision. The old name still appears in some backup
folders.

---

## Running it

**Installed build** — run the shortcut. First launch opens on Setup.

**From source** — `WISPWASP.bat`, or:

```
.venv\Scripts\python.exe app.py
```

Useful flags:

| Flag | What it does |
|---|---|
| `--listen` | start listening immediately |
| `--panel setup` | open on a particular panel (`live`, `prompt`, `gallery`, `setup`, `settings`) |
| `--demo` | fake backend: no GPU, microphone or ComfyUI needed |
| `--selftest` | check the install and write a report, then exit |

`--selftest` is the first thing to run when something is wrong on someone
else's machine. It writes to `%USERPROFILE%\WispWasp\selftest.log`.

---

## How it fits together

```
audio out  ->  loopback capture  ->  whisper  ->  prompt  ->  ComfyUI  ->  overlay
```

One worker thread does all of it. That is deliberate: a typed prompt is
drained from the queue before the next listening cycle can start, so
manual prompts pause listening as a consequence of the structure rather
than through a lock, and two GPU jobs can never overlap.

| File | Responsibility |
|---|---|
| `avcore/engine.py` | the worker thread, all state, the manual queue |
| `avcore/audio.py` | WASAPI capture, and the sound-activation gate |
| `avcore/process_audio.py` | capturing one application, via COM |
| `avcore/speech.py` | WAV decoding, transcription, prompt building |
| `avcore/images.py` | ComfyUI and Pollinations backends |
| `avcore/server.py` | serves the overlay page, state and images |
| `avcore/setup.py` | first-run downloads and detection |
| `avcore/comfy_launcher.py` | starts and stops ComfyUI |
| `avcore/catalog.py` | which prompt made which image; kept prompts |
| `avcore/styles.py` | named suffix styles, built-in and user-made |
| `avcore/config.py` | settings.json, data folder location |
| `avgui/` | Qt front end; panels hold no state of their own |
| `avgui/customize_panel.py` | colours, styles, decorative themes |
| `avgui/decorations.py` | the layer a theme is painted on |
| `avgui/themes.py` | the themes themselves, and their registries |
| `avgui/sounds.py` | generated sound effects, per theme |
| `avgui/widgets.py` | tally light, level meter, hover caption, folding box |
| `avgui/dialogs.py` | the two confirmations |
| `hooks/rthook_av_stub.py` | stands in for PyAV, which is not shipped |
| `tools/7zr.exe` | unpacks the ComfyUI download |
| `assets/` | logo, wordmark and icon |

**Panels never store state.** Each has one `on_state(snapshot)` method and
redraws from what it is given. That is what lets the window tear panels
down and rebuild them in a different layout mid-session without losing a
render in flight.

That rule has been broken twice, both times the same way: a panel kept its
own list, and a second route into the same action bypassed it. The Prompt
panel held its own prompt history, which the always-visible strip never
touched. If a list belongs to one panel but can be changed from elsewhere,
it belongs in the engine.

---

## Where files go

| | Running from source | Installed |
|---|---|---|
| Settings | `.wispwasp-data\settings.json` | `%LOCALAPPDATA%\WispWasp\settings.json` |
| Overlay images | `.wispwasp-data\output\` | `%LOCALAPPDATA%\WispWasp\output` |
| Recording scratch | `.wispwasp-data\scratch\` | `%LOCALAPPDATA%\WispWasp\scratch` |
| Prompt catalogue | `.wispwasp-data\prompts.json` | `%LOCALAPPDATA%\WispWasp\prompts.json` |
| Kept prompts | `.wispwasp-data\favourite_prompts.json` | alongside the above |
| Style presets | `.wispwasp-data\styles.json` | alongside the above |
| Theme sounds | `.wispwasp-data\sounds\` | generated on first use, not shipped |
| Crash log | `%USERPROFILE%\WispWasp\crash.log` | same |

Source builds before this layout wrote runtime files directly into the
repository root. On first source run, WispWasp moves any recognizable legacy
runtime data into `.wispwasp-data\` when it can do so without overwriting
anything.

Typed prompts save wherever Settings says; blank means the Desktop, read
from the registry so OneDrive redirection is honoured.

---

## OBS

Both capture methods work off the same server.

**Browser Source** — sidebar, "Copy browser URL", paste it in. The URL is
`http://127.0.0.1:8420/overlay.html?bare=1`; `bare=1` hides the status
readout so only the image shows.

**Window Capture** — sidebar, "Open overlay window", then capture that
browser window. This one keeps the status readout visible.

Images crossfade over 1.4s. The page polls `state.json` every 2s.

---

## Capture sources

Three kinds, all selected from one list in Settings:

| Kind | What it captures |
|---|---|
| `output` | everything playing through a speaker or headset |
| `input` | a microphone or line in |
| `process` | one application and its child processes |

Switching applies from the next clip. The recorder owns an open audio
handle, so it is rebuilt between cycles rather than swapped mid-recording.

**A device name is not unique.** "Razer Kraken V4 X" is both an output and
a microphone, so the kind is stored alongside the name and matched first.
Resolving by name alone picks whichever comes first, which is not
necessarily the one you chose.

**Per-application capture** uses Windows process loopback, which needs
build 20348 or newer. Only programs that have opened an audio stream are
listed, because one that has never made a sound cannot be captured. The
target is stored by executable name, not by process ID, since IDs do not
survive a restart - otherwise choosing Discord would work until you next
closed it and then silently capture nothing.

The process ID given to Windows is the top of the application's tree.
Browsers, Discord and Steam all play audio from child processes, so
capturing the child alone would miss whatever a sibling plays.

### The COM detail that cost a day

`ActivateAudioInterfaceAsync` returns `E_ILLEGAL_METHOD_CALL` if the
completion handler is not **agile**. The error says nothing about
threading and sends you hunting through apartment models, struct layouts
and pointer validity - none of which are the problem.

Windows delivers the activation callback from another apartment, so the
handler must implement `IAgileObject`. The C++ sample gets this free by
deriving from `FtmBase`; in Python it has to be declared. It is a marker
interface with no methods, so it looks like dead code. It is not - remove
it and per-application capture stops working entirely.

What found it: activating an *ordinary* endpoint failed the same way,
proving the fault was in the call rather than in process loopback; then
passing a NULL handler returned `E_INVALIDARG`, proving the function was
reachable and objected specifically to the handler object.

Also note comtypes returns `[out]` parameters rather than filling in
pointers passed as arguments, so `GetActivateResult` takes no arguments
and returns a tuple.

### When a clip starts and stops

Two modes, set by **Capture** in Settings. Only one applies at a time, so
the other's settings are disabled rather than left looking adjustable -
and a disabled control needs an explicit `:disabled` colour in the
stylesheet, because setting `color` unconditionally overrides Qt's own
disabled palette. Greyed-out controls that still look lit were exactly
that mistake.

**Fixed clips** take a clip of set length every cycle, whether or not
anything was said. Cycle length is measured start to start.

**Sound activated** waits for the level to pass a trigger, records while
sound continues, and stops once it has been quiet for long enough or the
longest-clip limit is reached.

`ActivationGate` in `avcore/audio.py` makes those decisions and nothing
else - both the ordinary recorder and the per-application one drive it.
A state machine fed a level can be tested directly, which is how its
edge cases are covered rather than hoped for.

Three behaviours that are not obvious and are each pinned by a test:

- **A brief dip does not end the clip.** Speech has gaps between words,
  and stopping at the first quiet chunk cuts people off mid-sentence.
- **Audio from before the trigger is kept**, about a third of a second.
  Speech begins quieter than the threshold, so without a run-up the first
  word is clipped off every time.
- **Short blips are discarded.** A door closing clears the threshold for
  a moment; the minimum length stops those becoming prompts. The skip
  reason says which limit applied.

The trigger level is the setting that needs tuning, and it depends
entirely on what else is making noise. The level meter on the Live page
is the tool for it: the notch is the silence gate, and you can watch
where speech sits relative to it.

---

## The gallery

Thumbnails come from two folders: the overlay folder, and wherever typed
prompts are saved. **A manual image exists in both** - once where you
chose to save it, and once as a mirror the web server can reach without
exposing the whole save directory. That is two files and one picture, so
the gallery lists it once, preferring the saved copy because the mirror
is subject to pruning.

`publish_overlay` copies any image into the overlay folder before showing
it. The page fetches images by name from that folder, so showing one that
lives elsewhere previously returned a 404 and left the overlay blank.

**Hovering** tints the image and shows the prompt that made it. Painted
rather than a tooltip, because a tooltip appears elsewhere on screen after
a delay and the point is connecting the words to the picture in front of
you. The tint is heavy enough for light text to read over a pale image and
light enough that the image stays recognisable.

Prompts are remembered in `prompts.json`, keyed by filename. Without it
nothing on disk connects an image to the words that made it - a filename
is only a timestamp. One index rather than a sidecar per image, because
manual images are saved wherever the user likes and scattering `.json`
files through someone's Desktop to support a tooltip is not a fair trade.
Images made before the catalogue existed say so rather than showing an
empty hover.

**The cog** appears with the caption and stays visible while its menu is
open - otherwise moving the pointer to the menu fades both away.

**Favourite images** are outlined in blue all the time, not just on hover,
since the point is spotting them while scanning. A favourite is honoured
in three places, and all three are tested because any one failing would
delete an image the user asked to keep:

- pruning skips them, and they do not count towards "keep last x"
- the catalogue's entry cap never evicts a favourite
- re-recording an image preserves the flag

**Renaming** moves both copies and carries the catalogue entry across -
everything recorded about an image is keyed by filename, so a rename that
did not move the entry would silently lose its prompt and its favourite
mark. Every destination is checked before anything moves, so a clash
cannot leave one copy renamed and the other not, and if the image is on
the overlay the page is pointed at the new name.

**Paging.** Every image on disk is listed; how many are *drawn* is a
separate question, answered by the picker at the bottom. There used to be
a hardcoded cap of 60 here, unrelated to any setting, so with "keep last"
above that the extra images - favourites among them - could not be
reached at all.

**Filters** sit above the grid: date, name, prompt, source and type, plus
a three-state favourites toggle. Left click shows only favourites, right
click hides them, and either button returns it to neutral from the state
it put it in. Favourite is a mark on an image rather than a kind of
image, so it combines with the rest instead of being one of the source
options - "heard, but not favourited" is a question worth being able to
ask. Name and prompt matching is forgiving: every word has to appear
somewhere in any order, failing that the whole thing is tried as a
subsequence, so "lghths" finds "lighthouse".

**Censoring** blurs a thumbnail until it is deliberately revealed. It is
a mark in the catalogue exactly like a favourite, so the file is never
touched and the choice is always reversible. The blur is done by scaling
the image down to a dozen pixels and back up rather than with a blur
filter: it is cheaper, indistinguishable at that size, and - the part
that matters - it genuinely discards the detail instead of hiding it
behind something that could be undone. It has its own three-state filter
beside Favourites, and the two combine.

**Nothing decorative is painted on a picture, wherever it lives.** The
live preview was excluded from the start, but gallery thumbnails and the
expanded viewer were not: patterned decoration avoided them because they
are labels, while the washes and tints went straight over. Decoration
now asks for every widget showing a generated image, and excludes the
*picture* rather than the widget - a thumbnail is wider than the image
inside it when the aspect ratios differ.

`tools/check_artwork.py` renders a flat white thumbnail under every
theme, plain and enhanced, and fails if any pixel drifts - it was
checked against the old behaviour first, which showed a drift of 35.

**The expanded viewer sits above the decoration rather than being cut
out of it.** Cutting a hole was the first attempt and it was wrong: the
effects stopped dead wherever the viewer opened, so they vanished on
expanding and popped back on collapsing. Being above means its dimmed
backdrop falls over the decoration as well, and everything behind the
picture goes quiet together instead of disappearing.

That is why the viewer is parented to the **window** and not to the
gallery: the decoration layer is a child of the window, and a child of
the gallery can never rise above one of the window''s own children. It
is given the gallery''s bounds rather than taking its parent''s, so it
covers the grid without swallowing the sidebar, and it closes if you
navigate away rather than being stranded over another page.

The controls anchor to the **picture**, not to the stage around it. A
portrait image leaves wide bars either side, and arrows placed on the
stage would float out in the backdrop away from the thing they act on.
Clicking anywhere off the picture closes it, letterbox bars included -
they are backdrop, not picture.

**Expanding.** The button beside the cog grows an image out of its own
thumbnail to fill the gallery, and collapses back to the same place. The
continuity is the point: a lightbox that fades in from nowhere loses the
connection between what was clicked and what is now shown.

Arrow keys and the on-image arrows step between pictures, sliding the
outgoing one out the way the incoming one arrives, so the pair reads as a
strip being moved rather than two unrelated fades. It stops at both ends
rather than wrapping, since silently looping back makes it impossible to
tell where the set ends. Escape or the close button collapses it.

The list it walks is **what is currently visible**, not everything on
disk. Arrowing through a filtered gallery should follow the filter, or
the filter would stop meaning anything the moment the picture got bigger.

A censored image opens still blurred, with the instruction in the middle;
clicking cross-fades the sharp image in over the top rather than swapping
the pixmap, because an instant switch after a deliberate click reads as a
glitch.

The gallery also re-reads the folder whenever it is navigated to. Images
can appear while another page is open, and a gallery that only updates
when something changes on screen looks stale.

**Purge** empties the gallery of everything not favourited, in one
action. The count comes from the engine rather than from what is on
screen: filters and paging mean the grid usually shows a fraction of what
exists, and deleting more than the user can see would be a nasty
surprise. The confirmation states both numbers - how many go and how many
are safe - because a vague warning before the most destructive action in
the app is not much of a warning. The button disables itself when every
image is a favourite, and says so.

**Deleting** clears the overlay first if that image is showing, and
removes both copies of a manual image. The dialog shows the image rather
than only its name - confirming by reading `live_20260912_214512_880.png`
is not really confirming - and **Deny is the default button**, so a stray
Enter cannot destroy a file.

---

## Prompt history

The list under the overlay shows what was heard or typed, newest first,
with a colour bar down the left edge:

| Colour | Meaning |
|---|---|
| red | skipped: silence, filler, a repetition loop, too short |
| green | heard from audio |
| blue | typed by hand |

A bar rather than coloured text, because the transcripts are the thing
being read and tinting them would make some harder to read for a reason
unrelated to their content.

**The kind is recorded when the entry is made**, not inferred afterwards
from whether a prompt exists. A typed prompt that gets skipped is red, not
blue - what matters is that nothing came of it.

**The style suffix is stored apart from the prompt** and drawn dimmed.
Once the two are concatenated they cannot be separated again, since a
style is just more words, so `base` and `suffix` travel separately from
both the live cycle and the manual queue. Rows are painted by a delegate
because a list item carries exactly one colour and two-tone text is not
otherwise possible.

Right-clicking a green or blue entry offers copy and favourite, each with
and without the style. Skipped entries get no menu: there is nothing to
copy or keep, and a menu of dead options is worse than none. "With style"
falls back to the current setting when an entry was made without one, so
it always means something.

**Kept prompts** live in `favourite_prompts.json`, deliberately separate
from the image catalogue - that one is keyed by filename and an entry dies
with its file, whereas a kept prompt outlives any image made from it. They
appear in a collapsible box above Earlier prompts, whose header keeps
showing a count while folded so collapsing it does not hide whether there
is anything inside.

Removing a favourite confirms with the same dialog shape as deleting an
image, Deny again the default.

---

## Applying settings

With `ui.confirm_settings` on, which is the default, nothing in Settings
is written until Apply is pressed. Edits are held as pending values with
the originals kept beside them, which buys three things:

- **Cancel can put every control back.** Each binder registers how to
  restore its own widget, so a combo box and a spin box are both handled
  without the panel knowing which is which.
- **A crash loses only what was never applied.** Nothing unconfirmed ever
  reaches disk, so closing the app is the same as cancelling.
- **Undoing an edit by hand withdraws it.** Setting a value back to what
  it was removes it from the pending set and the bar slides away, rather
  than asking about a change that no longer exists.

Side effects wait too. Changing the capture device does not touch the
recorder until Apply, and the layout does not rearrange itself around a
decision that has not been made - which would be particularly jarring,
since it moves the Apply button.

Leaving Settings with changes in the air is refused. Rather than a dialog,
the window border flashes blue and the bar trembles: enough to point at
the thing that needs answering without stealing focus. Apply and Cancel
each glow their own colour on the way out, so the bar says which way it
went instead of simply vanishing.

Turning the option off restores the older behaviour, where each change
lands the moment it is made.

The bar and the vignette are children of the window, not of the Settings
panel, so they float above whatever is showing and survive a layout
switch. One consequence worth knowing when testing: `QWidget.render()`
skips a widget carrying a graphics effect, and the bar has one for its
glow - so it is missing from anything captured that way. Use
`QScreen.grabWindow` instead.

---

## Customize

A sub-page under Settings, which unrolls in the sidebar when that section
is open and rolls back up when it is not. Settings is about what the app
does; this is about how it looks, which is a different question.

**Two colours carry the interface.** `ui.theme_main` is the colour of
doing something - the primary button, the marker beside the open page.
`ui.theme_accent` is the colour of noticing something - favourites,
selected text, the attention flash. Everything else is derived, so there
are only ever two choices to make.

The status colours are deliberately **not** adjustable. Red meaning live
and amber meaning working are the app telling the truth about its state,
and a status that can be repainted can be made to lie. There is a test
asserting they have not moved.

The stylesheet is built by a function rather than being a constant,
because the palette can change while the app is running. Anything that
captured a colour at import time would keep serving the old one - which
is exactly what `KIND_COLOURS` in the history list used to do, and why it
is now a lookup performed when each row is drawn.

**Style presets** are named prompt suffixes. Built-ins ship with the app
and cannot be edited or removed; the user's own live beside them in
`styles.json` and can be. They are one list because the person choosing
one does not care where it came from. Picking one is published through
the engine, so the picker in the Prompt panel and the list in Customize
cannot disagree about which is in use.

---

## Decorative themes

A theme is painted on `DecorationLayer`: a mouse-transparent child of the
window, above the panels and below the change bar. Three rules govern
what it may cover, and they are the whole design:

- **generated artwork always wins**
- **controls and text always win**
- decoration fills whatever is left

### The two regions

There are two clip regions, not one, and the difference matters.

- **patterned** things - wallpaper, webs, glyphs, stars - avoid the
  artwork, every control, and every label.
- **placed** things - the background tint, candles, crabs, the eye, the
  divider colours - avoid only the artwork.

A candle's glow passing near a label is fine. A rectangular bite taken
out of that glow to dodge the label is not: it reads as a fault rather
than as care. But nothing at all may cross a generated image, including a
wash - painting the tint outside the artwork region produced visible
banding across every picture, and the static mock-ups could not show it
because they were painted onto a screenshot where the tint was already
part of the image.

Geometry is measured a few times a second and cached, not computed per
frame: it walks the whole widget tree, which is fine at 5Hz and wasteful
at 14. The artwork cut-out follows the **picture**, not the widget
holding it - a preview is wider than the image inside it, and excluding
the whole widget leaves bare strips either side.

### After a layout switch

`setCentralWidget` adds a fresh child, and Qt stacks new children above
existing ones - so the decoration ends up underneath the interface, with
cached regions measured from panels that no longer exist. Both have to be
put right together, which `_lift_overlays` does for the decoration, the
vignette and the change bar. Anything parented to the window rather than
to a layout needs the same treatment.

### What a theme brings

| Piece | Where |
|---|---|
| painter | `PAINTERS` in `themes.py` |
| options it offers | `OPTIONS` |
| what it calls them | `OPTION_LABELS` |
| control shapes | `SHEETS` |
| sound set | `EVENTS` in `sounds.py` |

A theme declares which options it has, so a theme without sounds never
shows a sound toggle. The two toggles are hidden rather than greyed out
when no theme is applied: a disabled "Spooky sounds" with no theme still
asks to be understood, and the answer is nothing.

Decoration may not paint on controls, so reshaping them through the
stylesheet is the only way a theme can reach them.

### Registries go last

Every registry names things defined above it, and each theme has been
added by appending code to the end of the file. Five themes, five broken
registries - three explanatory comments did not prevent the fourth or the
fifth. Run `tools/move_registries.py` after adding one; it moves the
registry section wherever it has ended up.

### What the decoration costs

Measured with `tools/bench_decorations.py`, which paints each theme at
full window size, both plain and enhanced, and reports milliseconds per
frame. The repaint timer runs at roughly 14fps, so the budget is 71ms.

| theme | plain | enhanced |
|---|---|---|
| halloween | 8.9ms | 12.9ms |
| starfield | 7.4ms | 16.0ms |
| underwater | 7.7ms | 30.6ms |
| kawaii | 14.6ms | 15.1ms |
| cryptic | 10.8ms | 12.7ms |
| aero | 9.0ms | 31.9ms |
| desert | 9.5ms | 9.2ms |

**The decoration paints on the UI thread**, so a heavy frame at full
rate leaves the interface itself sluggish. The enhanced artwork ran at a
slower pace for a while because of it - and that was a mistake worth
recording: fewer frames of a moving thing reads as lag in its own right,
whatever the thread is actually doing, so it felt worse rather than
better. Buffering the layered light halved the real cost instead, and
both sets run at the same rate again.

The phase advances by real time rather than a fixed step per frame, so
the animation keeps its speed whatever the rate. Without that, slowing
the timer would slow the drift of the light rather than just updating it
less often.

**Layered light is drawn small and scaled up.** Nested bands remove the
hard sides a single filled shape would have, but every band keeps an
edge of its own, and across a wide beam those edges show as steps -
measured at up to fifteen levels of brightness on the underwater shafts,
which reads as faint coloured wedges in the water. `_through_buffer`
renders that work into a quarter-size image and scales it back up, so
the interpolation removes every one of those edges. It is also far
cheaper: underwater enhanced fell from 57ms to 31ms, aero from 33 to 19,
arctic from 31 to 21. More bands would have fixed the banding and cost
more; this fixed it and cost less.

Widening the underwater shafts also taught what is actually expensive
here: **overdraw, not geometry**. Cutting the samples per ribbon from 48
to 16 saved barely a fifth, while four more layers cost half as much
again - wide translucent shapes stacked over each other blend the same
pixels a dozen times over.

Underwater enhanced is now the heaviest, at roughly 42ms: its sun
shafts are five stacks of six sampled ribbons, each with its own
gradient. Note the figures move by ten milliseconds or more between runs
depending on what else the machine is doing, so treat them as an order
of magnitude rather than a measurement - trimming the shafts from six
stacks of seven saved far less than the arithmetic predicted, because
the noise is larger than the saving.

Aero enhanced is the one to watch: the feathered swooshes are eight
sampled polygons each rather than one filled shape, which is most of
that number. It still leaves room, but it is the first element where the
cost was worth measuring rather than assuming - and the figure above is
a worst case, since the benchmark paints the whole window with no clip
region excluding artwork and controls.

### Two sets of artwork

Every theme is drawn twice. The **plain** set is what everyone sees; the
**enhanced** set - shaded, jointed, with highlights - is behind an opt-in
toggle beside Fancy and the sounds.

Both are kept deliberately. The plain shapes are lighter to draw, and a
sparser look is a legitimate preference rather than a mistake to be
corrected, so this is a choice and not an upgrade path. Nothing changes
for anyone who does not go looking.

The flag is threaded down through the drawing helpers rather than
duplicating each painter. Each helper takes `rich=False` and branches at
the top, so the two versions of a spider live next to each other and the
call sites stay identical. Every painter signature ends with the same
flag, defaulting off, which means a caller that has not been updated
keeps the original artwork instead of breaking.

The cryptic glyphs are the one element with no enhanced twin. A more
legible mark was drafted and rejected: it drifted towards looking like a
real alphabet, and the cruder original is stranger, which suits the
theme better. Quality and fitness are not the same question.

### Animation and sound

"Fancy" starts and stops a repaint timer. Painters read a phase that
simply stops advancing, so with it off the decoration is still drawn and
perfectly still - there is no separate frozen version to keep in step.

Sounds are opt-in, silent by default, and additionally gated on a theme
being active. Callers name the **event** - `listen_start`, `cleared`,
`page`, `egg` - and the theme decides what that sounds like, so a door
creak never turns up in a starfield. Every sound fires on a transition
rather than a value: the engine publishes state many times a second, and
tying a sound to "is listening" rather than "started listening" chatters
for as long as it keeps listening.

The effects are generated into the data folder on first use rather than
shipped. `QtMultimedia` must not be excluded in the spec - it was, once,
and the packaged build simply made no sound with nothing to say why. The
self-test now checks for it.

### Drawn, not assembled

Themes are drawn the way an illustration would be: one closed silhouette,
then shading inside it. The kawaii clouds began as overlapping flat
circles and read as a diagram of a cloud; united into a single path with
a graded fill, a bright rim and two highlights, they read as a drawing of
one.

Two things a pen cannot do, both solved the same way. A stroked arc
carries one flat colour, so a rainbow drawn that way stops dead at each
end, and drawing it in short segments of varying alpha leaves visible
stripes - neighbouring segments must overlap to avoid gaps, and the
overlap doubles the alpha at every seam. A **conical gradient** sweeps by
angle around a centre, which is exactly how an arc is described, so the
fade follows the bow itself. The same trick fades the cryptic sigil
rings.

The cryptic eye follows the cursor. It costs one cursor query per frame -
the layer is already repainting, so nothing extra is drawn. The iris is
clamped to an ellipse inside the lens so it never leaves the eye, it
tracks vertically as well as horizontally because horizontal-only looks
like a doll, the catchlight stays put as a real one would with a fixed
light source, and the gaze returns to drifting when the pointer leaves
the window. With Fancy off the layer stops repainting, so the eye holds
still.

---

## Getting models

The app ships without one. Setup fetches a first model, and more can be
added later from the catalogue - `avcore/models.py` knows how to ask
what exists and how to bring a file down, and deliberately knows nothing
about widgets so it can be tested without a screen.

**Civitai is the catalogue.** It answers the questions someone actually
has before committing to several gigabytes: what is it, how big is it,
what is it based on, what may I do with it - and it gives a direct
download URL. HuggingFace was tried first and rejected: its model
listing does not reliably report file sizes, and a browse list that
cannot say how big something is before you press the button is not much
use. Measured from the live API: SD 1.5 checkpoints are about 2GB
(fp16) or 4GB, SDXL about 6.5GB. There is no realistic 1GB checkpoint,
so the size choices offered should stop at two.

**Downloads are written to resume.** These files take an hour on a slow
line and someone will close the app part way through, so it writes to a
`.part` file, asks for the rest with a Range header next time, and only
moves it into place once all of it has arrived. A truncated checkpoint
that looks complete is the worst outcome: it fails at load time in a way
nobody can diagnose. A server that ignores the Range header and sends
the whole file again is handled too, since appending in that case would
corrupt what was already there.

**The adult toggle filters the list. It does not restrain a model.** A
capable checkpoint can still produce adult images whatever the catalogue
says about it, and the wording in the interface says so rather than
implying a guarantee. Nerfing a model is not possible in any honest
sense - the capability is in the weights, and prompt or classifier
filters fail in both directions - so the choice is curation, not
castration.

**The first choice in Setup is how images get made at all**: online, or
on this computer with one of two models. It sits above the install
checks because it decides whether any of them matter - someone who picks
online should not then be staring at a list of things to download.

**Models already on disk are recognised by their weights, not their
filename.** Almost nobody''s SDXL checkpoint is called sd_xl_base_1.0,
and looking for that exact name reported a folder of perfectly good
models as empty. SDXL carries two text encoders under
`conditioner.embedders`; SD 1.5 has a single `cond_stage_model`.
Reading the safetensors header tells them apart whatever the file is
called, costs about a hundredth of a second, and is cached against size
and modification time.

What setup fetched is told apart from what was already there. A model
found on disk satisfies the option, but only the official file is
offered for removal - deleting somebody''s own checkpoint from a button
marked *Uninstall Stable Diffusion XL* would be a nasty surprise, so
that belongs in the model manager where it is named.

The page re-checks while it is on screen, so a model arriving from the
browser, from Settings or from Explorer shows up without a restart. It
polls only while visible, and only refreshes when the answer changes.

Each local option carries its own Install and Use buttons. Install
fetches that model (and ComfyUI with it, if needed); once it is there
the same button becomes Uninstall, and Use switches generation to it
without a trip to Settings. Both are confirmed, and both say what they
will cost or free.

Online is what is left when nothing local is installed, so it is
selected rather than merely offered - a new install can generate
something immediately instead of sitting at a download prompt.

Settings mirrors this. Under *Generate with* is a *Model* row listing
only the installed options, and choosing Pollinations hides both that
row and the whole ComfyUI section: generating online uses neither, and
leaving those on screen invites fiddling with settings that cannot do
anything.

Only official releases are offered there. A community checkpoint can be
withdrawn by its author, which is a poor thing to discover during
first-run setup; anything else is a browse away. The sizes are measured
from the hosts rather than estimated, and there is deliberately no 1 GB
or 2 GB choice - the smallest official checkpoint is about four
gigabytes, and the 2 GB files people have seen are community fp16
conversions, which belong in the catalogue.

An unset choice falls back to the previous default, so an existing
install does not change model on upgrade.

**Browsing and managing are two tabs of one window**, reached from *Get
more models* beside the checkpoint picker in Settings. Finding a model
and deciding you have too many are the same errand, and the disk figure
on one side is what makes the other side make sense.

Each tab keeps its own status line. A single shared one meant a search
finishing in the background wiped out the message about what had just
been deleted.

Removing warns when the model is the one currently selected, and clears
that setting so generation falls back to whatever is found first rather
than pointing at a file that is gone. A delete that fails because
ComfyUI still has the file open says so, rather than showing a raw
Windows error that reads like a fault in this app.

**An API key is optional.** Most checkpoints download without one; a few
are gated. `models.civitai_key` is attached as a bearer token when set,
and a refusal is reported as needing an account rather than as a crash.

---

## Building

```
.\build.ps1
```

That runs the tests, builds the app, verifies the build is not stale,
runs the self-test against the built binary, then compiles the installer.

**Do not run ISCC on its own.** Inno packages whatever is sitting in
`dist\` with no warning, so compiling without rebuilding first ships stale
code. That has already happened once. The staleness check in `build.ps1`
exists to make it impossible, and has since blocked two bad installers.

Output: `installer\WispWasp-0.1.0-setup.exe`, about 65 MB, expanding to
roughly 250 MB.

### Packaging notes that cost time to discover

**PyAV is not shipped.** faster-whisper imports it at module load but only
uses it inside `decode_audio`, which is never called now that
`avcore/speech.py` reads its own WAVs. A runtime hook supplies a stub,
saving about 62 MB. The stub raises on any real use, but lets dunder
lookups raise `AttributeError` normally — without that, Python's import
machinery probes `av.__wrapped__` and the app dies at startup.

**py7zr cannot open the ComfyUI archive.** It is built with the BCJ2
filter, which py7zr does not implement. `tools\7zr.exe` is used instead:
public domain, under 600 KB, handles BCJ2 and long paths.

**Windows path length.** PyTorch's licence folder nests past 260
characters. `LongPathsEnabled` is off by default on Windows, so
`setup.long_path()` applies the `\\?\` prefix. Without it, extraction
fails partway through — after a 1.8 GB download.

**`silero_vad_v6.onnx`** ships inside faster-whisper as package data and
must be collected explicitly, or `vad_filter=True` fails at runtime.

**Read-only files defeat rmtree.** Git pack files inside ComfyUI are
marked read-only, and `rmtree(ignore_errors=True)` skips them silently.
`setup.remove_tree()` clears the bit and retries.

**opengl32sw.dll is kept deliberately.** It is 20 MB of software OpenGL
that a working GPU never loads, but without it Qt can fail to start on a
machine with no usable driver. A blank window that cannot be reproduced
locally is not worth 20 MB.

---

## Tests

```
.\build.ps1              # everything, including the build
.\.venv\Scripts\python.exe test_engine.py     # or any one suite
```

| Suite | Covers |
|---|---|
| `test_wav.py` | sample width conversion, downmix, resampling (FFT-checked) |
| `test_mic.py` | device enumeration by kind, microphone capture |
| `test_process_audio.py` | per-app capture, including that it excludes other apps |
| `test_activation.py` | the sound gate, mode switching, disabled styling |
| `test_engine.py` | queue behaviour, GPU serialisation, scratch file isolation |
| `test_recovery.py` | ComfyUI going away, restarting, and the startup warm-up |
| `test_server.py` | overlay routes, path traversal, port clashes |
| `test_clear.py` | clearing the overlay without touching the file |
| `test_layouts.py` | layout switching without losing state or destroying widgets |
| `test_catalog.py` | the prompt catalogue and hover captions |
| `test_models.py` | finding models, and downloading them without losing work |
| `test_options.py` | favourite images, deleting, gallery deduplication |
| `test_sync.py` | prompt history syncing across panels, colour coding |
| `test_favourites.py` | kept prompts, style splitting, the right-click menu |
| `test_setup.py` | detection, resume, range handling, cancellation |
| `test_install.py` | full install flow against a local server |
| `test_core2.py` | prompt building stays verbatim |
| `test_confirm.py` | holding settings changes until they are applied |
| `test_customize.py` | the Customize sub-panel and the adjustable palette |
| `test_docs.py` | this document against the code it describes |

`test_docs.py` is the one that keeps this file honest. It checks that
every settings key named here exists, that every test file listed is
present, that the table above covers what the build actually runs, and
that a handful of hard-won explanations have not been quietly deleted.
Documentation drifts silently, and a confidently wrong document is worse
than none.

Not run by `build.ps1`, because they are slow or need the network:

| Suite | Covers |
|---|---|
| `test_real_audio.py` | speaks through SAPI and checks the transcript |
| `test_live_download.py` | proves GitHub and Hugging Face honour resume |
| `test_full_install.py` | downloads all 8.3 GB, renders, then deletes it |

---

## Benchmarks

RTX 5070, SDXL, 20 steps. First render of a session costs 5-8s extra
while the checkpoint loads into VRAM.

| Resolution | Warm render |
|---|---|
| 384x216 | 5.5s |
| 1024x576 | 7.7s |
| 1344x768 | 3.7-8.1s |

Full resolution is nearly free compared to tiny, and below about 1
megapixel SDXL draws anatomy badly. Default is 1344x768 on a 22s cycle.

Transcription is 1.3-1.7s per 10s clip with `small.en`.

---

## Settings worth knowing

| Key | Note |
|---|---|
| `audio.mode` | `cycle` or `activation`; the other mode's settings grey out |
| `audio.cycle_seconds` | start-to-start gap, fixed clips only |
| `audio.silence_rms` | below this a clip is skipped as silence |
| `audio.activation_rms` | level that starts a sound-activated clip |
| `audio.activation_silence` | quiet needed to end one |
| `audio.activation_max` | hard limit on a single clip |
| `audio.activation_min` | shorter than this is treated as a blip |
| `audio.device_kind` | `output`, `input` or `process` |
| `speech.min_words` | shorter transcripts are discarded |
| `image.style_suffix` | appended to **every** prompt, including overheard speech |
| `image.keep_images` | counts only images that are not favourited |
| `image.manual_auto_push` | whether typed prompts go straight to the overlay |
| `comfyui.autostart` | start ComfyUI with the app, at launch not on first render |
| `ui.layout` | `hybrid`, `sidebar` or `split`; changes take effect immediately |
| `ui.confirm_settings` | hold settings changes until Apply is pressed |
| `ui.theme_main` | the "do something" colour; blank means the built-in |
| `ui.theme_accent` | the "notice something" colour |
| `ui.decor_theme` | decorative theme; `none` by default |
| `ui.decor_animate` | the Fancy toggle: whether decoration moves |
| `ui.decor_sounds` | opt-in, and silent unless a theme is active |
| `ui.decor_enhanced` | opt-in, richer artwork in place of the plain set |
| `ui.gallery_page_size` | thumbnails drawn at once; 0 means all |
| `image.style_name` | which preset the current suffix came from |
| `ui.live_split` | where the image / history divider was left |
| `ui.favourites_open` | whether the Favourite prompts box is folded |
| `ui.autostart_listening` | start listening as soon as the window opens |

Prompts are **verbatim**. An early version stripped filler and truncated
to 18 words, which mangled what people actually said. It now only rejects
junk and never rewrites.

Junk filters: an RMS silence gate, a list of phrases Whisper invents over
silence ("Thank you", "Subscribe"), a minimum word count, and a
repetition check that catches stuck loops like "we are, we are, we are".

A word on `image.style_suffix`: it is appended to whatever the microphone
picks up, and live speech often contains real people's names. Combining an
explicit style with live capture can produce a sexual image labelled with
someone who never agreed to it. The app warns about this in Settings; the
notice can be turned off under Interface.

---

## Keeping images

`image.keep_images` counts **only images that have not been favourited**.
A favourite is never deleted however low the limit goes, and does not
count towards it - so with a limit of ten and five favourites, fifteen
images stay on disk. That is deliberate, but it is invisible: the number
next to "Keep last" looks like a total, and a gallery holding more than
it says looks like the setting being ignored. Settings says so in words,
with the actual count of favourites, updated as they are marked.

Lowering the limit asks before deleting anything. The question is put at
**Apply**, not when the number is typed: in confirm mode nothing is real
until then, and warning about a deletion that might never happen is worse
than not warning. Denying withdraws that one setting and leaves the rest
of the pending changes alone.

---

## Interface conventions

The window sits beside OBS in a dark room, so the palette is a slate
instrument panel. Each colour means one thing, everywhere:

| Colour | Means |
|---|---|
| red `TALLY` | live on air; a history entry that was skipped; errors |
| amber `WORKING` | the GPU is busy; input level below the gate |
| green `OK` | input level above the gate; heard entries; Confirm |
| blue `FAVOURITE` | favourite images and favourite prompts |
| red `DANGER` | destructive actions: Delete, Deny |
| navy `SELECTION` | selected text |

Red is the one to be careful with. It is meant to mean *live* or *wrong*,
and every extra use drains that meaning. Text selection was red for a
while, which made every ordinary drag through a prompt look like an
alarm; it is now a muted navy. Red still appears on the primary button
and the active nav marker, which is arguable - those are "go" and "you
are here", not warnings.

Two rules that are easy to break by accident:

**A disabled control needs an explicit `:disabled` colour.** Setting
`color` unconditionally in the stylesheet overrides Qt's disabled
palette, so a greyed-out widget renders fully lit. Both the label and the
field need it, or half the row looks active.

**The safe option is the default button.** In both confirmations Deny has
focus, so Enter never destroys anything.

The logo is cyan and violet, which does not match the amber and red of
the interface. Three colour families is one too many; retuning the accents
to the artwork - keeping red for the tally, where it is broadcast
convention - is still outstanding.

---

## Safe mode

One tick, five layers, none reliable alone. Safety terms on the negative
prompt; blocked words refused before generating; adult models hidden;
every picture scored by a bundled classifier; anything flagged blurred
in the gallery and kept off the overlay. Off by default.

**The layers that matter are the deterministic ones.** The classifier is
probabilistic and its true-positive rate is unmeasured here - there was
no honest way to test it without generating the thing it is meant to
catch. What can be relied on is the model filter and the prompt gate,
and a checkpoint merged for explicit output defeats everything else.

The first version of this was barely connected, and the pattern is worth
remembering: every hole was a quieter route to the same place. The
listening cycle refused blocked prompts while typed ones sailed through.
The pickers hid adult models while "first found" asked ComfyUI directly
and took its first answer - which was the adult model the lists were
hiding. The checkpoint cache was keyed on the setting, which stays blank
for "first found" either way, so a model resolved before the tick went
on was handed back after it. Guard where the decision is made, not where
it is displayed.

The safety terms are added when a picture is generated, never written
into ``image.negative_prompt``, so turning safe mode off gives back
exactly the wording somebody chose. That is right and invisible, which
is its own fault - hence the line under the Avoid field saying what is
being added. It refreshes when settings *land*, not when the tick moves,
because Settings holds changes until Apply.

## Keyboard shortcuts

Single keys on the Live and Prompt pages, changed under Settings then
Customize. Space starts and stops listening, V captures, R repeats, C
cancels, X clears the overlay.

Three rules make them safe to have at all. They fire only on those two
pages, because the gallery has its own keys and the other pages are full
of fields. They never fire while somebody is typing - checked by
behaviour rather than by widget name, since pressing X in a spin box is
still typing. And a modified press is left alone, so Ctrl+C stays copy
whatever C is bound to.

Binding a key that is already taken is refused, naming the action that
holds it. Two actions on one key means one of them quietly stops
working, and finding out which by experiment is nobody's idea of a good
time.

These are window shortcuts, not global hotkeys: they only work when the
app has focus. Intercepting keys from the rest of the machine would be
overreach, particularly on a computer that is also driving OBS.

## Sizes, and a 32-bit signal

Two separate faults made download progress lie, and neither was a
counting error.

The first was units. The app divided by 1024^3 and wrote "GB", while
HuggingFace, Civitai and every download page count 1000^3 - so a 6.9 GB
checkpoint appeared as 6.5 GB. That reads as the app disagreeing with
the page the file came from, which is worse than a rounding error
because there is no way to tell which is lying.

The second was worse. The download workers declared
``progress = Signal(int, int)``, and Qt's ``int`` is 32 bits, so any
file over 2,147,483,647 bytes wrapped: a 4.27 GB model arrived as
-29,870,600 and the bar read "0.14 of -0.03 GB". The dangerous part was
not the negative. A 9.6 GB model wrapped to 5.3 GB - wrong, but
plausible enough that nobody would ever report it. Both signals are
``qint64`` now.

## Animating a still

An extra rather than part of the app: another 8.9GB and a card that can
hold it. Nothing is fetched and the menu entry stays hidden until
somebody turns it on in Setup, because the point of an opt-in is that
people who do not want video never pay for it.

Stable Video Diffusion is the model because it is the only family that
fits 12GB without quantised weights and offloading. It takes an image
and nothing else - no prompt can steer it - and it produces no sound.
Models that accept a prompt for image-to-video want more memory than
this card has, and the only open model generating synchronised audio in
one pass needs a text encoder larger than the whole card.

**The lengths offered are the ones that worked, measured rather than
reasoned about.** A longer clip only fits if each frame is smaller:

    frames   landscape    seconds   measured
       25   1024 x 576      2.5       192s
       50    768 x 432      5.0       199s
       75    640 x 360      7.5       206s
      100    512 x 288     10.0       172s

Asking for 50 frames at the full 1024x576 does not merely run slowly.
The per-step time went 6s, 18s, 34s, 76s as it spilled out of memory,
and then ComfyUI aborted - the process, not the job. Trading size for
length keeps the total work roughly constant, which is why every option
lands near three minutes.

That crash is also why a run of refused connections while polling is
read as ComfyUI having died rather than waited out: sitting through a
thirty minute timeout after the far end has gone is indistinguishable
from the app hanging.

The progress bar counts against the measured estimate rather than
against real progress, because ComfyUI offers none over HTTP. Once it
passes the estimate it says "any moment now" rather than showing a full
bar that sits there, which would be a lie of a different kind.

Posters for the gallery are written by ComfyUI at the same time as the
clip, and a clip's length is recorded in the catalogue. Nothing decodes
video at runtime: PyAV is deliberately excluded from builds to save
sixty megabytes, and a feature that works only when run from source is
worse than no feature.

---

## A label that fought the layout

The preview is a QLabel that rescales its pixmap on every resize. A
QLabel reports its size hints from whatever pixmap it holds, so the
layout asked how big it wanted to be, the answer changed, the layout
resized it, and round again. In the split layout that feedback was
visible as the divider jumping about under the hand as soon as an image
appeared, and it got worse the larger the picture: a 1920-wide image
demanded 927px from a panel happy with 320px a moment before.

`sizeHint` was already pinned. `minimumSizeHint` was not, and that
is the one the layout uses for its floor. Both are now fixed and the
size policy is `Ignored`, so the contents never get a say in the size.

Two things made this hard to find. Driving the splitter with
`setSizes` does not reproduce it - the splitter consults its children
while the mouse is down, and skipping that machinery skips the fault.
And the numbers looked innocent from outside: the travel did not change,
because a different widget happened to be setting the floor.

**The floor itself was its own bug.** `_fit_status_bar` sheds the
meter, the counters and three buttons as the panel narrows and is
written down to about 330px, but the layout never let it get there: the
minimum was worked out while everything was still visible, so it
reported 760px, so the panel could not narrow, so nothing was ever
hidden. Saying plainly how narrow the bar may go breaks the circle - the
divider went from 193px of travel to about 900px.

---

## The controls beside the prompt box

The few settings people change while working - backend, model, size,
steps, guidance - sit in a compact strip directly above the prompt box
on both the Live and Prompt pages, with the style and overlay choices on
a second row. They write immediately: there is no Apply, and none of
them needs one, because the next image simply uses the new value.

They were in the side bar first. That was wrong for two reasons: a
column of controls competed for width with everything else, worst of all
in the split layout, and the decision belongs where the eye already is
when deciding what to make. Only the layout picker stayed in the bar,
because that one is about the window rather than about the next image.

There are three views of the same settings - the Live strip, the Prompt
strip and the Settings page - and changing any one updates the others.
They read and write the same values, so they cannot disagree; what they
need is only to be told to re-read.

Adding the second row cost the transcript list a few pixels at small
window heights, because Qt takes them from the list rather than the
preview, which has a minimum. That is recorded honestly in the tests
rather than papered over.

## What made each image

Every generated image records the backend and checkpoint that made it,
captured at the moment of generation - the setting can change
afterwards, and then nothing on disk would say where an older picture
came from. It shows in the gallery caption, the hover text and the
expanded viewer, and the gallery can be filtered by it.

That filter is built from the pictures on hand rather than from what is
installed: a model may have been deleted, or the images may have come
from another machine, and the question is what made these. Images from
before this was recorded get their own key rather than an empty one -
sharing the empty string with "any model" made choosing them quietly do
nothing.

## Settings profiles

A profile keeps a named set of preferences - *Streaming*, *Quiet room* -
so the alternative to twenty adjustments is one click. They live in
``profiles.json`` beside the settings.

Three things are deliberately left out. **Where ComfyUI and the folders
are**, because those describe the machine rather than a taste, and
restoring a stale path would send the app looking for an install that is
not there. **The Civitai API key**, because it is a credential and
copying it between files that get shared or backed up is how credentials
leak. **Which model tier was installed**, because that is a fact about
the disk.

Loading only touches the keys a profile actually carries, so one saved
before a setting existed leaves that setting alone rather than reverting
it to a default it never knew about.

Saving is refused while the confirm bar is holding changes: a profile
records what is in effect, and storing what is merely waiting would be
quietly wrong.

**Reset to defaults keeps the installation paths.** Every preference
goes back to how the app ships, but not where ComfyUI lives - a reset
that could lose track of a ten gigabyte install and start downloading it
again is a far bigger consequence than that button promises. The
confirmation says what survives as well as what goes, because the real
worry with a reset button is usually "will I lose my pictures".

---

## Releases and updating

**Push before tagging.** A release creates its tag from whatever the
branch head is at that moment. Publishing v0.1.7 before pushing the
commit tagged it at the previous commit - the release carried none of
the work it was named for, and the check that downloads and verifies it
passed against something that later vanished. The order is: build, test,
commit, push, confirm nothing is unpushed, then release.

**A failed asset upload leaves a ghost.** Three attempts to upload
``WispWasp-0.1.7.1-setup.exe`` returned HTTP 500 while the identical
bytes went up cleanly under another name, which is what isolated it: the
first failure left a record holding that filename, invisible to the API
and untouched by ``--clobber``. Deleting the release and recreating it
clears the records. If an upload 500s more than once on the same name,
it is the name that is stuck, not the file.

**Verify by downloading, not by reading the response.** The upload
saying "uploaded" is GitHub's word for it. The check that matters is
fetching the published manifest with no credentials, downloading the
installer it points at, and comparing the SHA-256.


The version is written in `avcore/version.py` and nowhere else. The
window shows it in the corner, the installer takes it as a parameter,
and `build.ps1` reads it to name the output. It used to live only in
installer.iss, so the running app had no idea what it was - which makes
a bug report much harder to act on.

Builds are published to GitHub Releases, each carrying the installer,
the read-me for a new person, and `latest.json` - version, download
URL, size and SHA-256. The app reads that manifest, and if a newer
version exists it says so and offers to open the page.

**It only ever tells you.** Nothing downloads or runs an installer.
Code that fetches and executes a binary on someone else''s machine is a
serious thing to own, and with no code signing behind it this app has
no business doing it. There is a test asserting the update module
contains no subprocess call and fetches no executable, because that is
the kind of thing that gets added later by someone being helpful.

The check runs on a thread, once per session when the Setup page is
opened, and on demand from the button. A background check that cannot
reach the server says nothing - nobody asked it a question - while
pressing the button and getting silence would just look broken.

Rolling back is why the page is linked rather than the file: older
releases stay there, and settings, images and models live outside the
program folder, so an older installer can be run over the top without
losing anything.

---

## Settings that were quietly ignored

Five bugs shipped together in the model-choice work, all the same shape:
a choice made in one place and ignored in another. Worth listing,
because the symptoms - *it keeps switching back*, *it generates with the
wrong thing* - are miserable to diagnose from outside.

**A refresh must not write settings.** The Setup page defaulted people
with no models to Pollinations from inside its refresh, which runs on a
timer. Anyone part way through installing a model was flipped back to
online every two seconds. Defaults belong in a one-time decision, not in
a function that runs repeatedly.

**Match on what is installed, not on the official filename.** The row
highlight compared the current checkpoint against `sd_xl_base_1.0`, so
a model the user had found themselves matched nothing and the tick was
dragged back to the first installed option on every refresh.

**The backend was built once and kept.** `Engine._backend` was created
on first use and never rebuilt, so switching between ComfyUI and
Pollinations changed nothing until the app restarted. It now tracks
which kind it built and rebuilds when the setting differs - while
leaving alone any backend handed in from outside, which is how the demo
stubs and the engine tests work.

**The checkpoint was cached outright.** `ComfyBackend.checkpoint()`
resolved once and kept the answer forever, so choosing a different model
did nothing until restart. It now caches against the setting it was
resolved from.

**A picker must offer values that exist.** The Settings model row wrote
the official filename, which on most machines is not the file that is
actually there. It now carries the real filename and shows it beside the
label.

**The Live counter says what happened, not how the code is shaped.**
It read *28 cycles, 18 images*, where a cycle was anything the app
listened to - whether it generated, was too quiet to bother with, or had
too few words to be worth a prompt. The gap between the numbers mixed
those together, and nobody outside the code could tell which they had
been getting.

It now reads *18 images from 28 listens*, with the reasons on hover:
what was skipped and why, and separately what failed. That separation is
the point - audio below the threshold is the app working correctly, a
render that fell over is not, and a single gap cannot tell you which.

Cancelled listens count as neither and are taken back out of the total,
so stopping something on purpose does not leave a hole that looks like a
fault.

**Stopping a job is not a failure.** ComfyUI files an interrupted job
under `error` like any other, and by the time the history is read the
cancel flag has usually been cleared - the Cancel button interrupts
ComfyUI directly rather than through the polling loop. Unless the
interruption is recognised in the history, pressing Cancel is reported
as *Generation failed* with the raw status list printed into the window.
Genuine failures now pull out the exception and the node that raised it
instead of dumping the structure.

The lesson that ties them together: anything cached from a setting needs
to remember *which* setting value it was built from, and anything that
runs on a timer must only read.

---

## Qt traps

Collected because each cost real time and each will recur.

**An animation set to `DeleteWhenStopped` outlives its Python name.** The
C++ object is gone but the attribute still points at it, so asking it
anything - even its state - raises `RuntimeError`. Guard the access, or
clear the reference when it finishes.

**`QWidget.render()` skips widgets carrying a graphics effect.** The
change bar has one for its glow, so it is simply missing from anything
captured that way. Use `QScreen.grabWindow` instead.

**`findData` compares wrapped Python objects by identity.** A tuple
rebuilt from settings never matches the equal tuple in the combo, and the
lookup silently returns -1. Strings happen to work, which is why only the
capture picker showed it. Compare by value instead.

**Setting `color` unconditionally overrides Qt's disabled palette**, so a
greyed-out widget renders fully lit. Explicit `:disabled` rules are
needed for both the label and the field.

**`isVisible()` is false for any widget whose window was never shown**, so
a test asserting something is hidden passes for the wrong reason. Use
`isHidden()`.

**A plain QWidget ignores its stylesheet background** unless
`WA_StyledBackground` is set. The viewer''s dimmed backdrop was
declared in the stylesheet and simply never painted; the gallery showed
straight through around every expanded image, which only became obvious
with a narrow picture.

**A widget takes its parent''s coordinate space, not the one you meant.**
Re-parenting the viewer to the window to get it above the decoration
left it taking the window''s rect for its geometry, so it swallowed the
sidebar.

**A stray non-ASCII character makes Qt drop a whole stylesheet rule**
without complaint. There is a test asserting every theme sheet is pure
ASCII; it has caught this three times.

---

## Dead ends

Recorded so they are not retried.

**Pollinations** removed `nologo`, `negative_prompt` and `enhance` from
their API on 2026-06-10. Images come back watermarked and the negative
prompt is ignored. It remains as a fallback backend only.

**Perchance** is behind Cloudflare Turnstile. Automating it would mean
defeating bot detection.

**Gemini / Imagen** — Imagen shut down 2026-08-17. The replacement has no
free tier (roughly $2-4/hour at this cadence), no `negative_prompt`, and
safety filtering that would block much of what unscripted speech produces.

**`audioop`** was removed in Python 3.13; RMS is computed with numpy.

**cu128 PyTorch** is required for the RTX 5070 — it is Blackwell,
`sm_120`. Verify with `torch.cuda.get_arch_list()`.

---

## Legacy

`legacy/` holds the scripts this grew out of — `listener.py`,
`prompt_tool.py` and their launchers. They are superseded, probably no
longer run, and will fight the app over port 8420 if started alongside it.
See `legacy/README.md`.

## Recovery and local backups

Git is the source-code recovery mechanism. Before risky work, make a focused
branch and commit the known-good state. `git status`, `git diff`, and
`git log --oneline --decorate` should be the first tools used to understand
what changed before anything is restored.

Local `backup\<date>-<name>\` snapshots can still be useful for generated
data or experiments that deliberately do not belong in Git. The `backup/`
folder is intentionally ignored, though, so repository recovery must never
depend on a hard-coded local snapshot being present.

If a source file needs to be recovered from an earlier commit, preserve any
current work first, then use Git to inspect or restore that specific version.
This keeps recovery traceable instead of silently replacing a hand-maintained
list of files from an increasingly stale snapshot.
