# QOL Updates/features

## Feature 1: Collating and combining
> **PINNED — deferred to Phase 4 (decision 2026-06-24).** The interactive editor depends on
> `PlaybackEngine` (Phase 3) + `WaveformView`/`SegmentOverlay` (Phase 4); building it before those
> exist = throwaway scaffolding, so build it on top of the GUI and reuse them. The one piece that
> could be pulled forward — "merge consecutive same-tone segments" — has no runtime value (the
> dispatcher already fires only on tone change), so it can wait too. When building: keep a
> non-merged "calibration copy" of the segmentation, and edit stored segments directly (don't
> re-run the segmenter) so it cooperates with the correction-preservation guard.
> See `docs/phase3-readiness-report.md` (Deferred). Feature 2 (cache cleanup) not yet evaluated.

- through the development process, a crital stage is the manual correcting segment labels to clean data for calibration. As I progressed through thsi process, a key problem quickly reared its head. Firstly the segmenting of a song itself seems set in stone, with no way to alter the timestamps for each segment, which sometimes either slightly lag behind or ahead of tone switches, and sometimes create unnecessary segments of 2-3 seconds for transitions or. Second, the number of segments generated seems to be high and often will have the same tone for multiple segments, though not a significant issue, it is largely inconvenient and redundant to have 3-4 or more consequtive segments with the same tone preset.  
As such, I propose the incorporation of a segment customization option in the correction tool, to fine tune segmentation and combine consequitive segments with the same tone preset for easier labeling and customization. The tool should work with a UI that allows for interactive timestamping and segmenting alongside playback, showing the track and allowing segments to be added and customized through clicks and options, similar to other GUI apps such as video editing software or DAWs such as ableton. Furthermore, it should present an option to combine segments labeled with the same tone, perhaps with a calibration copy of the initial segmentation too aid calibration.  

## Feature 2: Cache Cleanup
When analyzing songs, the first step is stemming to isolate guitar as much as possible for more accurate, robust, analysis. The issue this presents is each song now takes an additional amount of space on disk, which can be large per song depending on length and quality of the audio. Though it may not present an issue with small libraries, that quantity snowballs up and could take massive storage as a player's library grows. As such, I find it important to create a cache expiration process to remove old stems for songs that have already been analyzed, corrected, and calibrated. The starting point, I think, could be a 30 day cycle, cleaning up cached stemms that are older than 30 days and have already been analyzed AND manually corrected, eliminating it's need to be stored apart from potential re-calibration.