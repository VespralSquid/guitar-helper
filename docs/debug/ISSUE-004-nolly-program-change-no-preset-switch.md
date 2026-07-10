# ISSUE-004 — Archetype Nolly receives Program Change but does not switch preset

**Status:** Open — **debugging PAUSED 2026-06-26.** App pipeline proven correct end-to-end (incl. MIDI arriving at the plugin's own input); break isolated to the Nolly VST3 not acting on host-delivered Program Change. Resume via the standalone (see next steps).
**Date:** 2026-06-26
**Component:** Runtime MIDI dispatch (Phase 3) — *external* integration with Neural DSP Archetype Nolly. No defect found in `guitar_helper` code.

---

## Symptom
The app's MIDI Program Change messages reach the Archetype Nolly plugin (confirmed at the plugin's own MIDI input), and the plugin has the 5 PC→preset mappings configured and saved, yet **the active preset does not change** when PCs arrive.

## Goal of the integration
At each tone boundary the app dispatches a MIDI Program Change so the amp sim switches preset live:
clean=PC0, crunch=PC1, metal=PC2, edge=PC3, overdrive=PC4 (`presets` table is source of truth; `other`=-1 → no dispatch).

## What is PROVEN WORKING (ruled out as the cause)
The signal chain is verified end-to-end up to the plugin's input:

1. **App → loopMIDI.** `MidoPort` opens output port `'loopMIDI Port 1'` and sends `program_change` on channel 0 (= MIDI channel 1). App-side dispatch logic was separately verified live (`MidiDispatcher` fires correct PCs at tone boundaries, on-change only, `other` holds). 27 unit tests green.
2. **loopMIDI → Cantabile input port.** Cantabile Lite "MIDI Monitor — Input Port loopMIDIIN" showed all 5 Program Changes, **Channel 1**, programs displayed 1–5 (Cantabile is 1-indexed; raw bytes are 0–4).
3. **Cantabile route → plugin input.** Cantabile "MIDI Monitor — **Archetype Nolly 1 (MIDI In)**" showed the same 5 Program Change events arriving **at the plugin itself**, Channel 1.

→ The break is entirely *inside* Nolly: it receives the PC but does not act on it.

## Environment / setup history
- **Ableton Live Intro** was the first host. It works for audio but **does not pass Program Change to plugins**, and the only native workaround (a Max for Live PC forwarder) requires Suite/M4L, which Intro lacks. Abandoned this host for tone switching.
- **Windows cannot create virtual MIDI ports natively** (`rtmidi` can on macOS/Linux only) → loopMIDI (or equivalent) is required for app↔host MIDI on Windows.
- Switched to **Cantabile Lite** (free VST host) hosting `Archetype Nolly.vst3`. Guitar audio in → Nolly → speakers works. Route: `loopMIDIIN → Nolly MIDI In`, `Nolly Output → Main Speakers`. Audio interface: Focusrite USB ASIO, 64 samples @ 44.1kHz.
- The Nolly **standalone app is NOT installed** (plugin-only install; install folder has manual/uninstaller only). VST3 at `C:\Program Files\Common Files\VST3\Neural DSP\Archetype Nolly.vst3`.
- Nolly MIDI Mappings confirmed present and saved in the Cantabile instance: 5 rows, Type = "Program Change Preset", Channel 1, PC 0–4 → presets `cleeeen`, `SAMURAI`, `Le4ding`, `Ethereal`, `edge of break up`.

## What was tried (chronological)
1. **Direct PC sweep PC0–4 to loopMIDI** — sent cleanly; no switch observed.
2. **Confirmed channel alignment** — app sends ch 0 (= MIDI ch 1); track filter, Nolly mapping, and both MIDI monitors all show Channel 1. No mismatch.
3. **Confirmed indexing** — Nolly mappings use 0-indexed "PC 0"…"PC 4", matching the app's raw bytes 0–4. Cantabile's 1–5 display is cosmetic only.
4. **Verified MIDI reaches the plugin** (not just the port) via the "Archetype Nolly 1 (MIDI In)" monitor — events present.
5. **Verified mappings exist and are saved** in this Cantabile instance (originally suspected they lived only on the abandoned Ableton instance, since Neural DSP MIDI maps are per-instance).
6. **MIDI learn experiment (partial)** — armed learn; results were inconclusive/confusing to read; revisit with a single-preset capture.
7. **Slowed cadence to 5s gaps** for clearer observation — still no switch.
8. **Bank Select + PC experiment** — sent `CC0=0, CC32=0` (Bank Select MSB/LSB) immediately before each PC, because both MIDI monitors showed a parallel "Program Change (Banked)" event. Result not conclusively confirmed before pausing.
9. **MIDI learn exact-capture attempt** — armed learn on the `cleeeen` row, sent PC0 repeatedly: **nothing captured.** Reinterpreted as *expected* — Neural DSP "learn" appears to capture **CC only** (knob-move); Program Change Preset rows are set manually via the PC dropdown (which is already done correctly). So this is not a failure signal and the manual mappings are in the right state.

## Remaining hypotheses
- **A. MIDI learn left armed** — with learn ON, the plugin captures/reassigns each incoming PC instead of acting on it; would exactly explain "received but no switch." Must confirm learn is OFF during playback.
- **B. Banked vs plain Program Change** — Nolly's "Program Change Preset" may require a Bank Select before the PC (the "Program Change (Banked)" entries hint at this). Being tested (#8).
- **C. Mapping value/preset binding subtly wrong** — the exact byte/channel Nolly expects differs from what's mapped. Best resolved by exact-capture learn (see next steps), which removes all indexing/channel ambiguity.
- **D. A global "enable MIDI / Program Change" toggle** in Nolly settings is off.

## Leading conclusion at pause
Everything upstream is proven correct and the manual PC mappings are in the right state, yet the VST3 won't switch. The most probable remaining cause is the **Nolly VST3 not acting on raw Program Change events delivered by the host** (hosting/VST3 PC-delivery gap), not any app or routing defect. The standalone removes this variable entirely.

## Next steps (priority order, for resume)
1. **Install the Nolly standalone** (re-run Neural DSP installer → enable Standalone component), set its MIDI input = `loopMIDI Port`, and retest PC0–4. Most likely to "just work"; also the definitive isolation test (standalone honors PC natively).
2. If staying in-host: conclude the Bank Select test (#8) and hunt for a Nolly global "respond to Program Change / MIDI enable" toggle.
3. Once a working receiver is confirmed, run the full app (`run_playback`) against an analysed track and verify auto-switching at tone boundaries.

## Notes for the product (Phase 5 setup guide)
This issue is a *setup/UX* problem, not a code defect, and it generalizes:
- **Hardware modelers** (Helix/HX/Kemper/Quad Cortex/Boss) accept PC over USB-MIDI natively → cleanest path, no host/loopMIDI.
- **Software amp sims** need a PC-passing host + loopMIDI on Windows. Document that **Ableton (esp. Intro) filters PC to plugins**; recommend Cantabile Lite / the vendor standalone instead.
- Consider a future **in-app VST hosting** spike (e.g. `pedalboard`) to switch presets in-process and remove the external host + loopMIDI entirely (latency must be measured first).
