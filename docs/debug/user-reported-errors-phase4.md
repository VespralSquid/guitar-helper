# User-reported errors — Phase 4 (ALL RESOLVED)

_Reported during the M1–M3 UI build. All three were fixed by the Phase 4 M0
rework; kept as the original wording, since the report below traces each
symptom to its cause._

| # | Symptom | Cause | Fixed by |
|---|---|---|---|
| 1 | Slow load times | Full SHA-256 + librosa decode ran inline on the Qt main thread | M0 — `decode()` on `LoadWorker`, `attach()` on the main thread |
| 2 | Audio choppiness | Two causes: no teardown of the previous engine, and pyqtgraph's 20 Hz repaint starving the audio callback of the GIL | M0 teardown + ISSUE-005 (`SegmentTimeline`) |
| 3 | Audio persistence / overlaid playback | `Application.load()` never tore down the previous runtime graph, orphaning a still-playing stream with no handle | M0 — `attach()` calls `stop()` before rebuilding |

Full diagnosis: `docs/Report/phase4-rework-report.md` §1–2 and
`docs/debug/ISSUE-005-choppy-playback-gil-contention.md`.

---

## Original report (verbatim)

# Error 1: slow loadtimes
    - loading a song can often be slow when pulling it up to edit and correct, taking multiple seconds 

# Error 2: Audio choppiness
    - Audio is often really choppy during playback
    - noticed the choppiness tends to go away after clicking and loading up a different song

# Error 3: Audio Persistence
    - The previously played song continues playback when switching between songs, which is fine, but going back too the initial song does not allow controling the playback anymore, rather it seems to start a different instance of the playback, resulting in the same audio playback being overlayed rather than pausing or stopping the previous playback(let me know if my description here is not clear) 
