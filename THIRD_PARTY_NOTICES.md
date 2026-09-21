# Third-party notices

WispWasp uses and interacts with software and model files that are not part of
the WispWasp project license. Those components remain governed by their own
licenses and terms.

This document is an engineering inventory, not a replacement for the license
texts shipped by those projects. Before publishing a release, the maintainer
should confirm that the packaged build still contains all notices and license
materials required by the versions actually being distributed.

## Bundled/runtime dependencies

The packaged application includes Python packages from
`requirements-lock.txt`, directly or transitively. Important components
include:

| Component | Role | License currently identified by the project |
|---|---|---|
| Qt / PySide6 | desktop user interface and multimedia | LGPL v3 / applicable Qt for Python terms |
| faster-whisper | speech transcription wrapper | MIT |
| CTranslate2 | Whisper inference runtime | MIT |
| ONNX Runtime | inference runtime used by dependencies/features | MIT |
| Flask | local overlay web application | BSD 3-Clause |
| Werkzeug | local HTTP serving stack | BSD 3-Clause |
| 7-Zip / `7zr.exe` | ComfyUI archive extraction | 7-Zip project licensing; the standalone extractor is redistributed separately from WispWasp code |
| OwenElliott/image-safety-classifier-xs | bundled image safety classifier (`assets/image-safety-xs.onnx`) | MIT, per the model repository metadata |

The bundled safety classifier originates from
`https://huggingface.co/OwenElliott/image-safety-classifier-xs`; WispWasp
renames its ONNX file for the local asset bundle. The upstream model card
states that classifiers can make mistakes and describes the model as an
NSFW/NSFL/SFW classifier.

The lock file also contains supporting/transitive packages. They retain their
own licenses even when they are not named in this table.

## Downloaded during setup

WispWasp can download software or model files after installation. Those files
are not relicensed by WispWasp.

- **ComfyUI** is a separate project and process. Its own repository and
  distribution terms apply.
- **Stable Diffusion 1.5** and **Stable Diffusion XL** checkpoints offered by
  first-run setup retain the model licenses published with those checkpoints.
- **Stable Video Diffusion** is optional and retains its own model license.
- **Comfy-Org/BiRefNet** is downloaded only when cut-out/background-removal
  support is requested. The Comfy-Org model repository declares MIT licensing
  for the repackaged BiRefNet weights.
- Models selected through the in-app Civitai catalogue retain the license and
  usage terms supplied by their creators. WispWasp displays available license
  information before download where the catalogue provides it.

A model being technically downloadable does not mean every use of that model
is permitted. The model's own terms remain controlling.

## Online services

The optional Pollinations backend and catalogue/download services such as
Civitai and Hugging Face are external services. Their terms and privacy
policies apply when WispWasp communicates with them.

## Release-maintainer checklist

Before distributing a new installer:

1. review changes to `requirements-lock.txt`;
2. review bundled binaries and data added by `wispwasp.spec`;
3. confirm the relevant third-party notices/license files are still included;
4. check any newly bundled media, model, font, icon, executable, or other asset
   for redistribution rights;
5. update this document when the dependency or distribution model changes.

The WispWasp project-level source status is documented separately in
[LICENSE](LICENSE).
