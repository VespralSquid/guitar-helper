# Guitar Helper

An offline desktop application that switches your amp sim's preset for you while a
song plays.

It analyses locally-stored audio files, splits each one into segments by guitar
tone, and stores the result. During playback it watches the position and fires a
MIDI Program Change at every tone boundary, so the amp sim moves from clean to
crunch to metal at the right moments without anyone touching a footswitch. A
PySide6 interface holds the library, a segment editor for correcting the
machine's labels, and a live log of every MIDI decision.

Nothing leaves the machine. There is no account, no streaming service and no
network call in the analysis, playback or dispatch path.

```
audio file ──▶ htdemucs_6s ──▶ guitar stem ──▶ 24 features ──▶ segment boundaries
                                                                     │
                                                        archetypes.json (your labels)
                                                                     ▼
                                                            tone per segment
                                                                     │
                                                                  SQLite
                                                                     │
      playback position ──▶ segment lookup ──▶ Program Change ──▶ loopMIDI ──▶ amp sim
```

## Status

Source-only. There is no installer and no frozen build yet — you set up a virtual
environment and run it from the repository. Packaging is planned but not done, so
everything below assumes a Python toolchain on the machine.

Windows 11 is the developed and tested platform. The audio, MIDI and analysis
layers are portable in principle, but the MIDI routing step below is
Windows-specific and nothing else has been verified elsewhere.

## Two setup facts that cost people the most time

Read these before installing anything. Both are external to the app, and both
produce the same symptom — messages that clearly leave the app and never change
the sound.

**1. Point the app at a VST2 or standalone amp sim, not a VST3.**
VST3 does not deliver raw MIDI Program Change to a plugin. Steinberg's design
requires the *host* to translate MIDI controller data into a VST3 parameter, so
the plugin never sees the Program Change itself. The message will show up in
every MIDI monitor along the way and the preset will still not move. Use the
VST2 build of your amp sim, or its standalone application. A hardware modeller
(Helix, HX, Kemper, Quad Cortex, Boss) takes Program Change over USB-MIDI
directly and is the cleanest path of all. The full investigation is in
`docs/debug/ISSUE-004-nolly-program-change-no-preset-switch.md`.

Also note that some DAWs filter Program Change on the way to plugins — Ableton
Live Intro does, with no workaround available in that edition. Cantabile Lite is
a free host that passes it through.

**2. loopMIDI must already be running, with a port named `loopMIDI Port 1`.**
Windows cannot create a virtual MIDI port on its own, so the app needs
[loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) to hand
messages to the plugin host. Start loopMIDI, create a port, and confirm the name
matches exactly — the default the app opens is `loopMIDI Port 1`. If the port is
absent, MIDI is unavailable for that session; use `--mock` (below) if you only
want to work on the library or correct labels.

Route it in the host as: `loopMIDI Port 1` → the amp sim's MIDI input.

## Requirements

- Python 3.14 (developed on 3.14.3)
- Windows 11 with loopMIDI, plus a VST2 or standalone amp sim in a host
- ffmpeg on `PATH`
- ~2 GB of disk for the separation stack, plus ~52 MB for the separation model
  weights
- MSVC build tools, only for `python-rtmidi` — it has no cp314 wheel and is
  compiled from source

## Setup

### 1. Virtual environment

```
python -m venv .venv
.venv\Scripts\activate
```

Activate it before running anything below.

### 2. Dependencies — three files, three jobs

The dependency list is deliberately split so that CI and a basic install stay
light while the heavy machine-learning stack remains optional to install but
mandatory to use.

| File | Contents | When |
|---|---|---|
| `requirements.txt` | librosa, numpy, soundfile, pydub, mutagen, sounddevice, mido, PySide6 | Always. This is what CI installs. |
| `requirements-runtime.txt` | `python-rtmidi` | To send real MIDI. Compiles from source on 3.14; tests substitute a mock port and never need it. |
| `requirements-separation.txt` | torch, onnxruntime, audio-separator and friends | To add songs at all. See the warning below. |

```
pip install -r requirements.txt
pip install -r requirements-runtime.txt
```

**The separation stack is not a plain `pip install -r`.** Open
`requirements-separation.txt` and follow its four numbered steps in order — one
package has to be installed with `--no-deps` and one import has to be satisfied
with a stub, because of missing Python 3.14 wheels. The file explains why at each
step.

**Separation is mandatory, not an enhancement.** The analyser only ever sees an
isolated guitar stem; the full mix is never analysed. This is measured, not
stylistic: classifying the project's own hand-labelled segments scores 0.82
accuracy from a stem and 0.46 from the full mix, and the failure is systematic —
drums and bass push nearly every tone into `metal`. Without the separation stack
installed, adding songs is blocked and the app will say so.

### 3. ffmpeg

`audio-separator` requires ffmpeg on `PATH`, and `.m4a` / `.mp3` decoding goes
through it. WAV, FLAC and OGG decode natively without it.

```
winget install --id Gyan.FFmpeg -e
```

### 4. Separation model weights

The htdemucs_6s weights (~52 MB) are downloaded on the first separation. By
default `audio-separator` puts them in a temporary directory, which disk-cleanup
tools remove — after which the next separation silently re-downloads or fails
offline. Pass `--model-dir` to keep them somewhere durable:

```
python -m guitar_helper.run_ui --model-dir C:\GuitarHelper\models
```

The weights are a third-party pretrained network and are unrelated to
`archetypes.json`, which is this project's own 3 KB tone calibration and is
tracked in the repository.

## Running

### The application

```
python -m guitar_helper.run_ui
python -m guitar_helper.run_ui --mock          # no loopMIDI; dispatch is logged, not sent
```

`docs/user-guide.md` walks through the three modes.

### Command-line tools

Every tool takes the same resource-location flags (`--root`, `--db`,
`--library-root`, `--stems-dir`, `--archetypes`, `--model-dir`).

| Command | Does |
|---|---|
| `python -m guitar_helper.run_analysis <file>` | Analyse one file, store it, print the segments and the `file_hash` |
| `python -m guitar_helper.run_batch <folder>` | Analyse a folder. `--recursive`, `--reanalyze` |
| `python -m guitar_helper.run_correction <file_hash>` | Interactive terminal segment editor |
| `python -m guitar_helper.run_calibrate` | Rebuild `archetypes.json` from your corrected segments |
| `python -m guitar_helper.run_playback <file>` | Play a track and dispatch MIDI without the UI. `--mock`, `--port-name`, `--channel` |
| `python -m guitar_helper.run_ui` | The application |

Re-analysis refuses to overwrite a track's manual corrections. The
`--discard-corrections` flag is the only override, and it exists because those
labels are the calibration ground truth — a full set has been lost to a careless
re-run once already.

After correcting more segments, `run_calibrate` recomputes the archetypes and
prints how many labels each tone has; re-analyse afterwards so stored segments
reflect the new calibration.

## Where files live

Every path derives from one root, so nothing depends on the working directory.
Precedence: an explicit `--root` flag, then the `GUITAR_HELPER_HOME` environment
variable, then the current directory.

| Path | Default | Holds |
|---|---|---|
| `--db` | `<root>/library.db` | Tracks, segments, corrections, playlists, presets, settings |
| `--library-root` | `<root>/music` | Where moved files are searched for by content hash |
| `--stems-dir` | `<root>/stems` | Cached guitar stems, keyed by file hash |
| `--archetypes` | `<root>/archetypes.json` | Tone calibration |
| `--model-dir` | separator's own default | htdemucs_6s weights |

Audio files are never copied or moved. A track's identity is the SHA-256 of its
contents, so renaming or relocating a file costs a re-locate, not a re-analysis.

## Tone → Program Change

| Tone | PC | Default preset name |
|---|---|---|
| clean | 0 | Clean |
| edge | 1 | Edge of Breakup |
| overdrive | 2 | Overdrive |
| crunch | 3 | Crunch |
| metal | 4 | Metal |
| other | −1 | Other (no dispatch) |

The order is a deliberate clean→metal gain ramp. `other` is the no-dispatch
tone: when a segment is labelled `other` the dispatcher holds whatever preset is
already active and logs the decision, because a held preset is less wrong than a
guessed one.

These are defaults. The mapping lives in the `presets` table and is editable in
the application's Output mode — set each row's PC to match the patch slots in
your own amp sim.

## Tests and linting

```
python -m pytest -q
python -m ruff check guitar_helper/
```

The suite runs in about 40 seconds and must stay there, which means **no test
ever runs a real separation or a real analysis** — both are minutes of work.
Stub them. CI runs ruff and pytest on `windows-latest` for every push to `main`.

## Contributing — coding conventions

These are not style preferences. Each one is a rule the codebase already broke
once, and the reason matters more than the rule — a convention without its
justification is a convention that gets worked around.

**Comment the *why*, never the *what*.** No comment unless the reasoning behind
a line is non-obvious from reading it. A comment restating the code goes stale
silently and then actively misleads; a comment recording why a non-obvious choice
was made is the only thing a future reader cannot reconstruct.

**Parameterized SQL only — never an f-string in a query.** Track titles, artists
and playlist names come from file tags and user typing. The moment a query is
built by interpolation, an apostrophe in a song title is a syntax error and a
crafted one is worse.

**`presets` is the single source of truth for tone → PC. Never hardcode a PC
number.** The user edits this table to match their rig. Any dispatch path that
carries its own copy of the numbers works perfectly until someone remaps a patch,
and then it sends the wrong preset with no error anywhere.

**The audio callback must not block.** No database access, no MIDI send, no UI
call, no allocation inside `PlaybackEngine._callback`. Anything that can wait on a
lock or the GIL from that callback produces audible dropouts — this is what
ISSUE-005 was, where a 20 Hz waveform repaint starved the audio thread and made
playback choppy. That is also why the waveform view no longer exists.

**SQLite belongs to the Qt main thread, and only to it.** `sqlite3` connections
are not shareable across threads, so worker threads never touch the store. The
MIDI dispatcher receives a plain dict of tone → PC; `SegmentLookup` takes a
snapshot at construction; the load worker decodes audio and hands the result back
for the main thread to persist. A worker that "just reads one row" is a
`ProgrammingError` in production and a race in the meantime.

**Re-seed with `INSERT OR IGNORE` or an upsert. Never DROP and recreate on
startup.** The database holds hand-corrected labels that took hours to produce
and are the calibration ground truth. A startup path that recreates a table is a
startup path that can delete them.

**Separation is mandatory.** `NullSeparator` is test-only. Production analysis
always runs on the guitar stem; see the accuracy numbers above for what happens
otherwise.

**No `Co-Authored-By` trailers in commits.**

Further reading before a substantial change: `docs/architecture/ARCHITECTURE.md`
has the as-built component map, the threading table, and the seventeen
load-bearing design decisions with their consequences. `docs/debug/` holds a
report per non-trivial defect — several of the rules above are there in full,
with the evidence.

## Documentation

| Path | Contents |
|---|---|
| `docs/user-guide.md` | Using the application: library, segment editor, MIDI output |
| `docs/architecture/ARCHITECTURE.md` | As-built map, threading model, design decisions |
| `docs/debug/` | One report per fixed defect, root cause included |
| `docs/plans/` | Implementation plans, current and historical |
| `docs/Report/` | Project reports, calibration and latency analysis |

## License

MIT — see `LICENSE`.

Third-party dependencies keep their own licences. The htdemucs_6s separation
weights come from the Demucs project and are not distributed with this
repository.
