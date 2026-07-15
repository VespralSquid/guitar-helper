# ISSUE-004 — Archetype Nolly receives Program Change but does not switch preset

**Status:** RESOLVED — 2026-07-15. VST3 format does not deliver raw MIDI Program Change to plugins; VST2 and standalone builds accept raw PC directly and switch presets as expected.
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
*None of A–D below were the actual cause — the break is VST3's architecture, not any of these application/routing issues.*
- **A. MIDI learn left armed** — with learn ON, the plugin captures/reassigns each incoming PC instead of acting on it; would exactly explain "received but no switch." Must confirm learn is OFF during playback.
- **B. Banked vs plain Program Change** — Nolly's "Program Change Preset" may require a Bank Select before the PC (the "Program Change (Banked)" entries hint at this). Being tested (#8).
- **C. Mapping value/preset binding subtly wrong** — the exact byte/channel Nolly expects differs from what's mapped. Best resolved by exact-capture learn (see next steps), which removes all indexing/channel ambiguity.
- **D. A global "enable MIDI / Program Change" toggle** in Nolly settings is off.

## Leading conclusion at pause
Everything upstream is proven correct and the manual PC mappings are in the right state, yet the VST3 won't switch. The most probable remaining cause is the **Nolly VST3 not acting on raw Program Change events delivered by the host** (hosting/VST3 PC-delivery gap), not any app or routing defect. The standalone removes this variable entirely.

## Root cause CONFIRMED (2026-07-15)
**VST3 architecture does not deliver raw MIDI Program Change to hosted plugins.** Per Steinberg's VST3 SDK, MIDI controller data (including Program Change) must be translated by the **host** into a VST3 "parameter" that the plugin exposes for automation; the plugin never receives a raw PC message directly in VST3. This is a deliberate Steinberg design decision (avoiding MIDI's "unclear and often ignored semantics" from colliding with parameter automation) — a genuine architectural difference from VST2, not a bug in the app or host.

This explains the original symptom end-to-end without requiring any of hypotheses A–D: Cantabile's MIDI monitor showed the PC arriving at "Archetype Nolly 1 (MIDI In)" because that monitor observes raw MIDI at the port level — but Nolly's VST3 build never converted that raw PC into a preset switch because in VST3's architecture that conversion depends on host-side PC-to-parameter mapping that isn't wired for this plugin in Cantabile (or isn't implemented in the plugin itself).

**Confirmed fix:** User tested VST2 and Nolly standalone (both bypass VST3's parameter-automation-only MIDI model) — Program Change works correctly and switches presets as expected in both. This matches the paused investigation's prediction exactly: the standalone was the definitive isolation test. Going forward: use the Nolly **VST2** build or the **standalone app** for tone-switching workflows; VST3 remains fine for audio processing and manual preset selection in the plugin UI.

**Sources:**
- [IMidiMapping — VST 3 Developer Portal](https://steinbergmedia.github.io/vst3_dev_portal/pages/Technical+Documentation/Change+History/3.0.1/IMidiMapping.html)
- [Can a VST3 instrument plugin receive MIDI program changes from the host? — Steinberg Forums](https://forums.steinberg.net/t/can-a-vst3-instrument-plugin-receive-midi-program-changes-from-the-host/871552)
- [VST3 and MIDI CC pitfall — Steinberg Forums](https://forums.steinberg.net/t/vst3-and-midi-cc-pitfall/201879)
- [Virtual midi problems with VST Neural DSP — Steinberg Forums](https://forums.steinberg.net/t/virtual-midi-problems-with-vst-neural-dsp/1002000)

## Next steps
Resolution complete. For full app tone-switching: use Nolly VST2 or standalone as the MIDI receiver. VST3-specific note: VST3 may not reliably deliver Program Change even when a MIDI-passing host is used correctly (see "Notes for the product" below).

## Notes for the product (Phase 5 setup guide)
This issue is a *setup/UX* problem, not a code defect, and it generalizes:
- **Hardware modelers** (Helix/HX/Kemper/Quad Cortex/Boss) accept PC over USB-MIDI natively → cleanest path, no host/loopMIDI.
- **Software amp sims** need a PC-passing host + loopMIDI on Windows. Document that **Ableton (esp. Intro) filters PC to plugins**; recommend Cantabile Lite / the vendor standalone instead.
- **VST3 specifically may not reliably deliver Program Change**, even when a MIDI-passing host is used correctly. The setup guide should **recommend VST2 or standalone builds** for tone-switching workflows, and flag VST3 as a possible gap to check for other plugins/hosts.
- Consider a future **in-app VST hosting** spike (e.g. `pedalboard`) to switch presets in-process and remove the external host + loopMIDI entirely (latency must be measured first).
