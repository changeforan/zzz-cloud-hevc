# Licensing

This repository contains two kinds of material.

## 1. The patches (`patches/`)

These are modifications to **Wine**, and are therefore licensed under the
**GNU Lesser General Public License, version 2.1 or (at your option) any later
version** — the same terms as Wine itself. The full text is in `COPYING.LIB`.

Each patch is a `git-format-patch` file: it contains only the diff against
Wine's own source, authored by this repository's contributors. No Wine source
files are redistributed here in full.

## 2. The scripts and test programs (`install-proton-hevc.sh`, `tools/`)

These are original, trivial works — a shell installer and two small
standalone D3D11 test programs. They are released into the **public domain**
under CC0-1.0; if your jurisdiction does not recognise that, treat them as
MIT-licensed. They contain no Wine code.

## What is NOT here

No compiled binaries, no Wine source tree, and **no files belonging to any
game or its publisher**. The reverse-engineering notes in the README describe
observed behaviour of a third-party program for interoperability purposes; no
proprietary code is reproduced.

## If you redistribute builds rather than patches

Building these patches produces a modified Wine. If you distribute the
resulting **binaries**, the LGPL obliges you to also make the corresponding
**complete source** available to recipients — in practice, publish the exact
Wine tree/commit plus these patches, or point at them. A Proton build also
bundles other projects under their own licences (DXVK, vkd3d-proton, FFmpeg,
and others); redistributing a whole Proton tool means honouring all of those
too, not only this one.

Distributing the patches alone, as this repository does, avoids that entirely:
each user builds locally and no binary is ever redistributed.

## Codecs

These patches implement **no video decoder**. They translate DXVA structures
into Vulkan Video structures and call `vkCmdDecodeVideoKHR`; all actual HEVC
decoding is performed by the GPU's fixed-function hardware through the Vulkan
driver. No bitstream parsing, entropy decoding, or transform code is included
or distributed here.
