# Guitar Helper — user guide

How to use the application: build a library, correct what the analyser got
wrong, and point each tone at the right patch on your rig.

Installation, loopMIDI and the VST2-not-VST3 constraint are in the repository
`README.md`. This guide assumes the app starts.

```
python -m guitar_helper.run_ui
```

Add `--mock` to work without loopMIDI. Everything except real MIDI output
behaves normally; dispatch decisions are still logged, so you can check the
timing of a track before the rig is set up.

---

## The window

Four regions, always present:

- **Mode sidebar** (left) — **Home**, **Analysis**, **Output**. These are the
  three screens this guide covers.
- **Main area** (centre) — the current mode.
- **Queue** (right) — what is playing now and what plays next.
- **Transport** (bottom) — Prev, Play, Pause, Stop, Next, a **Loop segment**
  checkbox, a seek slider and the position readout.

The transport and the queue are shared by all three modes, so you can keep a
song playing while you edit its labels or watch MIDI go out.

### Playing something

Double-click a song in Home. The rest of the list it came from becomes the
queue.

Loading happens on a background thread, so the window does not freeze on a long
file; the library and queue are disabled for the moment it takes.

### The queue

Double-click a queue entry to jump to it. **Up** / **Down** reorder,
**Play next** moves the selected entry to the front of the remaining queue,
**Remove** drops one entry, **Clear** empties it. **Shuffle** toggles, and
**Repeat** cycles through its modes — the button shows the mode it is currently
in.

The queue is for listening. It is separate from the playlist you are working
through in Analysis mode, so stepping through corrections does not disturb what
is playing, and vice versa. The queue is not saved between sessions.

---

## Home — the library

Playlists on the left, songs on the right, a summary line at the top reading
`N tracks · M segments · K fully corrected`.

**Library (all songs)** is always the first entry in the playlist list. It is a
view of everything, not a real playlist — it cannot be renamed or deleted, and
songs cannot be removed from it except by removing them from the library
entirely.

The song table shows **Title**, **Artist**, **Date added** and **Progress**.
Progress is `corrected/total` segments, which is how you tell at a glance which
songs still need attention. A song showing `no segments` has a database row but
nothing analysed.

### Add songs to your library

Adding a song runs the full analysis: separate the guitar from the mix, extract
features, find the tone boundaries, label each segment, store the result. It is
the expensive operation in the whole application — expect **several minutes per
song**, dominated by separation.

From Home, open the add-songs dialog. You pick files or a folder, and set:

- **Recursive** — descend into subfolders when you picked a folder.
- **Re-analyse existing** — songs already in the library are skipped by default;
  this overrides that. Songs with manual corrections are *always* skipped
  regardless, and are reported as skipped. The application never offers to
  discard your corrections; that override exists only as a deliberate
  command-line flag.
- **Target playlist** — add each analysed song straight into a playlist.
- **Pause playback during analysis** — analysis is heavy, in-process work. If
  you hear dropouts while a batch runs, turn this on. The setting is remembered.

Before anything expensive starts, the dialog runs a preflight check and shows
what it found:

| Finding | Effect |
|---|---|
| Separation stack missing | **Blocks.** Nothing can be added — see the README. There is no analyse-anyway option. |
| Separation model weights missing | **Blocks.** A damaged install, not something to work around. |
| ffmpeg missing | Warning. `.m4a` and `.mp3` will fail; WAV, FLAC and OGG still work. |
| Stem cache or database directory not writable | **Blocks.** Better to know now than four minutes in. |
| A file that is missing, unreadable, or an unsupported format | That file is excluded, with a reason next to it. The rest proceed. |

You cannot start while a blocker is present or when no usable file is left.

A progress dialog then shows which song is being worked on, `n of m`, and the
current stage. Separation carries a note that it takes several minutes — that
stage looks stalled and is not. Each song lands in the result list as done,
failed or skipped, and one bad file never aborts the batch.

**Cancel** stops after the song in flight. It does not kill work mid-separation:
the button changes to *Finishing current song…* and the dialog stays responsive
until that song completes or errors. Songs already analysed before you cancelled
stay in the library.

Files are never copied or moved. Only their location is recorded, and a song is
identified by the SHA-256 of its contents — rename or relocate it and the app
finds it again by hash.

### Organise into playlists

**New playlist** asks for a name; names must be unique. Right-click a playlist
to delete it (deleting a playlist never deletes songs). Right-click a song for
**Add to playlist**, and — when you are viewing a playlist rather than the
Library — **Remove from this playlist**, which only unlinks it.

### Remove a song from the library

Removing a song is permanent and deletes its segments and every manual
correction along with it. The confirmation names how many corrected segments
will be destroyed, because that is the number worth thinking about — those
labels are also the calibration data behind every future analysis.

The cached guitar stem is deliberately kept. Adding the same file again reuses
it, so a remove-and-re-add gets you fresh labels in seconds rather than minutes.
That is the supported way to start a song's analysis over.

### Correct labels

Select a playlist (or the Library), select a song, and use the correct-labels
button under the playlist list. It opens Analysis mode with that song loaded and
remembers the list, so you can walk the whole playlist without coming back here.

---

## Analysis — correcting a song's labels

The analyser is right most of the time, not all of the time. This screen is
where you fix it, and every fix improves the next analysis: your corrections are
the calibration data `run_calibrate` fits the tone archetypes to.

The header shows the playlist context — `Playlist — Title (3 of 12)` — with
**◀ Prev song** and **Next song ▶** to move through it.

### The timeline

A colour-coded bar of the whole track: one block per segment, coloured by tone,
with the playhead drawn over it.

- **Click** anywhere to seek there. The segment you clicked becomes selected.
- **Hover** to see the tone under the cursor as a tooltip.
- **Drag a boundary** — the cursor changes to a horizontal resize arrow when you
  are close enough to one — to move the edge between two adjacent segments. The
  two segments stay touching: one grows and the other shrinks. Boundaries cannot
  cross their neighbours, and the track's start and end are not draggable, since
  there is no segment on the other side to move with them.

Below it, the segment table lists **Start**, **End**, **Tone**, **Confidence**
and **Corrected**. Selection is shared: clicking a row highlights that block on
the timeline, and vice versa.

Low **Confidence** is where to look first — it is the analyser telling you which
calls it was least sure about.

### Fixing labels

With a segment selected:

- **Relabel** — choose the right tone from the dropdown.
- **Confirm** — the label is already right. Mark it corrected without changing
  it. This matters: a confirmed segment counts as ground truth for calibration,
  an unconfirmed one does not.
- **Confirm All** — mark every segment in the track as corrected. Only worth
  using once you have actually checked them all.
- **Merge** — collapse the selected segment together with the run of adjacent
  segments sharing its tone into one. Use it when the segmenter split a single
  passage in two. If neither neighbour shares the tone, the status bar says
  there is nothing to merge.

Merging is a physical delete, but the pre-edit segmentation is snapshotted first
so calibration keeps the original granularity. You lose nothing by merging.

**Exclude from calibration** marks the whole track as not-ground-truth — useful
for a song you have labelled quickly or roughly, so it does not skew the
archetypes. Unlike everything else on this screen, this checkbox is written to
the database immediately.

### Listening while you edit

Use **Loop segment** on the transport. It loops whichever segment is currently
selected, so you can hear a boundary or a borderline label repeatedly while you
decide. Changing the selection changes what loops.

### Saving

Every edit is held in memory until you save. While anything is pending the
screen shows **Unsaved changes** and both buttons light up:

- **Save** writes all pending edits in one transaction.
- **Discard** throws them away and reloads the track from the database.

Navigating with Prev song / Next song, or opening another song for correction,
asks before discarding unsaved edits. Two paths do **not** ask — double-clicking
a song in Home and the queue's own Prev/Next — so save before you go looking for
something to listen to.

Two things to know after saving:

- **The dispatcher does not pick up saved edits until the track is reloaded.**
  Segment lookup snapshots the segments when a track loads. Reload the song to
  hear the corrected boundaries drive MIDI.
- **Corrections do not change classification on their own.** They change the
  data that `run_calibrate` fits archetypes to. To make them affect future
  analyses, run `python -m guitar_helper.run_calibrate` and then re-analyse.

---

## Output — MIDI mapping and diagnostics

This is the screen to open when the amp does not switch. It answers three
different questions, and it is worth knowing which is which.

### Point a tone at a different patch

The **Tone → Program Change** table is the single source of truth: nothing in
the app has a second copy of these numbers. Edit the **PC** cell of a row to
match the patch slot in your amp sim, and the **Preset name** cell to whatever
that patch is actually called on your rig — the name is yours to use, it does
not affect dispatch.

Edits are checked before they land, and a rejected edit explains itself in the
status bar:

- PC must be between 0 and 127.
- No two tones may share a PC.
- `other` must stay at −1 and nothing else may use −1. The classifier assigns
  `other` when the guitar stem is near-silent, or when no tone is a clear enough
  winner to call. Sending a preset on that would switch your sound on a guess,
  so the dispatcher holds whatever is already active and logs the decision
  instead.

If the mapping has drifted from the shipped defaults, a banner says which tones
differ and what the default was, and a **Reset to defaults** button appears next
to it. Rows you edited yourself are treated as deliberate and are never
overwritten by an upgrade.

### Prove the chain works

Select a row and press **Send test PC**. That Program Change goes out
immediately, without playing anything. It is the fastest way to isolate where a
silent rig is broken:

- Nothing arrives in your host's MIDI monitor → loopMIDI, the port name, or the
  host's input routing.
- It arrives but the preset does not change → almost always VST3. Use the VST2
  build or the standalone (see the README).
- It works here but not during playback → the problem is the segments, not the
  MIDI. Check the log below.

The button is disabled for `other`, which has nothing to send.

### Read the dispatch log

Every decision the dispatcher makes, in order, with a timestamp — including the
decisions to do nothing. That is the point: a silent amp with a silent log is a
different problem from a silent amp with a busy log.

| Entry | Meaning |
|---|---|
| `sent` | A Program Change went out: tone → PC. |
| `hold` | The tone did not change, or `other` is active. Nothing was sent, deliberately. |
| `UNMAPPED` | A segment's tone has no row in the preset table. Nothing was sent — this one is a real gap. |
| `gap` | The playhead is between segments. Holding. |

**Active preset** above the list shows what is currently in effect and since
when. **Clear log** empties the view.

The log keeps the most recent 200 events and only refreshes while this screen is
visible, so switching to Output mid-song shows the recent history rather than
starting blank.

### Fix the timing

**Dispatch offset** sends each Program Change slightly *before* its tone
boundary, compensating for the delay through loopMIDI, the host and the plugin.
The default is 75 ms; the range is −500 to +500 ms.

Set a value, press **Apply**, and listen. If the switch lands late, increase it;
if the new tone arrives before the riff does, decrease it. **Revert** restores
the previous value, so an experiment that sounded worse costs nothing to undo.
The label under the control always states what is actually in effect.

Calibrate this once for your rig. The value is stored and reused.

---

## When something is wrong

| Symptom | Cause to check first |
|---|---|
| MIDI is unavailable this session | loopMIDI is not running, or its port is not named `loopMIDI Port 1`. Start it and restart the app. |
| Program Changes arrive but the preset never moves | VST3. Use the VST2 build or the standalone amp sim. Some DAWs also filter PC to plugins. |
| Adding songs is blocked | The separation stack or its model weights are missing — the preflight report names which. |
| Adding an `.m4a` or `.mp3` fails | ffmpeg is not on `PATH`. |
| Nearly every segment is labelled `metal` | Analysis ran on the full mix rather than a guitar stem. Remove the song and add it again with the separation stack installed. |
| Saved corrections do not change the MIDI being sent | Reload the track — segment lookup snapshots at load time. |
| Corrections do not change future analyses | Run `run_calibrate`, then re-analyse. |
| Playback stutters while a batch is analysing | Turn on *pause playback during analysis* in the add-songs dialog. |
| The progress dialog seems frozen on a song | Separation takes minutes. The stage line says so. |
