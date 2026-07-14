## Intro 
    The current UI plan and implementation for this project works as an all in one correction and playback tool, with little to no separation of responsiblities. This leads to various issues with the app, for example playback and loading lag that is caused by the lack of separation between playback and librosa analysis, and the presistence of played tracks. The new proposed architecture specification intends to correct this issue. 

## Parts of the new UI 
    The new UI is intended to have three major parts and components within to handle various responsibilities: 
    Home
        1. Presentaiton window (home) - the starting point to view the library 
    Media
        2. library manipulation - component to allow manipulation of library, i.e. creation and deletion of playlists moving songs from one place to another
        3. playback engine - component to handle playback of songs, independent of analysis. Loading audio files, buffering, playing, pausing, seeking
        4. queue management/playback controller- create and manage song queue, with shuffle option to randomize playback order and other control options such as skip, repeat, go back to previous song
    Analyisis 
        5. Correction mode - create a window to view analysis mode, either for an entire playlist or select songs 
        6. correction load tool - load in the librosa breakdown and mappings from phase 3 components
        7. correction tool - allow remapping and manipulation of the song segmenting and tone
    Output 
        8. MIDI config - window to view the current midi configuration, allow mapping of tone labels to channels and "switches" i.e. PC, CC, etc. 
