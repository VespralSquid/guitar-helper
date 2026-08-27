# Third-party notices

Guitar Helper is distributed as a frozen bundle containing the components below.
MIT, BSD and ISC all require their copyright and permission notices to be
preserved in redistributions, which is what this file is for.

Versions are those bundled in the 1.0.0 build.

## Application libraries

| Component | Version | Licence |
|---|---|---|
| PySide6 (Qt for Python) | 6.11.1 | **LGPL v3** — see note below |
| NumPy | 2.4.6 | BSD-3-Clause |
| SciPy | 1.17.1 | BSD-3-Clause |
| scikit-learn | 1.9.0 | BSD-3-Clause |
| librosa | 0.11.0 | ISC |
| numba | 0.65.1 | BSD-2-Clause |
| llvmlite | 0.47.0 | BSD-2-Clause |
| soundfile (libsndfile) | 0.14.0 | BSD-3-Clause |
| sounddevice (PortAudio) | 0.5.5 | MIT |
| mido | 1.3.3 | MIT |
| python-rtmidi | 1.5.8 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| **mutagen** | 1.47.0 | **GPL-2.0-or-later** — see note below |

## Separation tier

| Component | Version | Licence |
|---|---|---|
| PyTorch | 2.12.1 | BSD-3-Clause |
| ONNX Runtime | 1.27.0 | MIT |
| audio-separator | 0.44.2 | MIT |
| pydub | 0.25.1 | MIT |

## Demucs and the htdemucs_6s model weights

The bundled file `models/5c90dfd2-34c22ccb.th` is the **htdemucs_6s** checkpoint
from Meta's Demucs project.

- Demucs code is MIT: "Demucs is released under the MIT license as found in the
  LICENSE file." Copyright (c) Meta Platforms, Inc. and affiliates.
- The MIT grant covers "the Software" and permits redistribution.
- **The licence text and README make no statement specific to the pretrained
  model weights**, as distinct from the source code. Redistributing the
  checkpoint under the same MIT terms is the common reading and nothing states
  otherwise, but it is not spelled out. Recorded here rather than assumed away.
- Upstream describes htdemucs_6s as an **experimental** 6-source model.

MIT permission notice, as it applies to Demucs:

> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

## Note on PySide6 (LGPL v3)

Qt is used under the LGPL. The build is PyInstaller **onedir**, so the Qt
libraries remain separate `.dll` files rather than being folded into a single
executable, which preserves the recipient's ability to replace them with a
modified Qt. Qt is not modified.

## Note on mutagen (GPL-2.0-or-later)

mutagen is used only to read title/artist tags from audio files
(`_read_tags` in `ui/analysis_worker.py` and `run_batch.py`).

**GPL-2.0-or-later is copyleft**, and unlike the permissive licences above it
places conditions on the combined distributed work, not just on mutagen itself.
Bundling it into this application and distributing that bundle is therefore not
compatible with offering the whole under MIT without further thought.

This is unresolved as of 1.0.0. The realistic options are to replace mutagen
with a permissively licensed tag reader (the usage is small and already
duplicated across two call sites), or to license the distribution accordingly.

## Not bundled

FFmpeg is **not** included in this distribution. It is a separate prerequisite
the user installs themselves, so its licence terms are not engaged here.
