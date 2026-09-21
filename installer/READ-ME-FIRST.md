# WispWasp - trying it out

WispWasp listens to what your computer is playing, and turns it into
images on an overlay you can put in OBS.

## Installing

Run the `WispWasp-<version>-setup.exe` installer you were given.

**Windows will warn you about it.** The installer is not code-signed, so
SmartScreen shows "Windows protected your PC". Click **More info**, then
**Run anyway**. That warning means "we have not seen this before", not
"this is dangerous" - but only install it if you trust where you got it.

**If it refuses to run at all**, with a message about an Application
Control policy, your machine has Smart App Control switched on. It
blocks unsigned programs outright. Turning it off works but cannot be
undone without reinstalling Windows, so do not do that on my account -
tell me instead and I will find another way to get it to you.

## First run

Open it and go to **Setup**. There is one choice to make:

- **Online - nothing to download.** Images are made by Pollinations.ai.
  No graphics card needed, nothing to install, works immediately. The
  images come back with a small watermark and can be slow when their
  service is busy. **Start here if you just want to see what it does.**

- **On this computer.** Better and faster images, no watermark, nothing
  leaves your machine - but it downloads ComfyUI and a model, which is
  about 10 GB in total and needs an NVIDIA graphics card. Stable
  Diffusion 1.5 is the lighter option, SDXL the better-looking one.

You can switch between them whenever you like, and install or remove
models from the same screen.

## Putting it in OBS

1. In WispWasp, click **Copy browser URL** at the bottom left.
2. In OBS, add a **Browser** source.
3. Paste the URL, and set the size to match your canvas.

The overlay stays transparent until an image appears.

## Using it

On the **Live** page, press **Capture**. It listens to whatever your
computer is playing, works out what it is hearing, and generates an
image from that. There is also a **Prompt** page if you just want to
type something.

**Gallery** keeps everything it has made. You can favourite images,
expand them, hide ones you would rather not show, and delete the rest.

## What to tell me

Anything that confuses you is worth reporting, especially in Setup -
you are the first person to see this who did not build it. Crashes,
things that look wrong, wording that does not make sense, all useful.

If something goes badly wrong there is a log at:
`%USERPROFILE%\WispWasp\` - sending me that helps a lot.

## Things I already know about

- The installer is unsigned, so Windows complains (see above).
- Online images are watermarked. That is Pollinations, not me.
- Generating locally needs an NVIDIA card. AMD will install but will not
  generate.
- This is still an early project. Assume rough edges and report anything confusing.
