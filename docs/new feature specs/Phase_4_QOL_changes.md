# Phase 4 QOL Changes
## Playlists and queue
    - add prompt to let user know if a song they're adding to a playlist is already in said playlist
    - create drag and drop queue manipulation and arrangement rather than ambiguous seeming "play next" button for UX.  

## Visuals
    - rework controls to cleaner, simpler UI, similar to spotify and apple music and other platforrms
        - eg. graphic icons for shuffle and repeat, pause, play, skip etc
        - Dev will add assets and icons folder for use
    

## Editing 
    - Add a visual indicator to show segments that have been modified before a save so that user can verify their changes
    - change the segment timestamping in segment table to appear as min:sec instead of sec. 
    -  Fine tuning the timing; I'd like to be able to manually type in the min:sec boundaries for segments rather than relying soley on dragging as dragging does not provide precision and is a little harder to be accurate with. If possible I'd like to integrate the use of arrow keys to shift boundaries by a few miliseconds at a time, longer if key is held on, similar to standard seek mechanics on media players.  

    - add calibrate button with confirmation pop up when clicked, alongside a way to revert to previous setting if calibration becomes incorrect. 
    - add unmerge/split segment option in case user would like to go back to inital segments or add additional segments for finer tone management
    - add ability to select multiple segments for manipulation; ctrl selecting or shift selecting
    - refresh button; as of now, when changes are made to the analysis, the song has to be reloaded by switching to a different song and back again before the changes can be tested. I'd like to be able to skip that process and either reload the song on save so the changes are immediately acted on by the software, or a manual reload button so the changes can be tested by users. 

## UX
    - add help tab to redirect to repo or documentation(to be created later, such as README and other helpful docs)
    - add right click menus to playlist content window with options to add songs, either from file or from a different playlist
    - Add preferences tab to top bar to allow configuration of playback settings, such as an option to count into songs by a user selected number of bars or the ability to hear a click track

##