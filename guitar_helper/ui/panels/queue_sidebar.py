"""Shell-level right sidebar (AC3): now-playing info + the manipulable queue.

Renders from QueueState via QueueStateBridge signals. Pure queue operations
(move/remove/clear/play-next/shuffle/repeat) go straight to QueueState — they
touch no Application/audio state. Starting playback of a queue entry needs
the shell's load pipeline, so that intent is emitted as `playAtRequested`."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from guitar_helper.db.interfaces import Track
from guitar_helper.ui.state.queue_state import QueueState
from guitar_helper.ui.state.state_bridge import QueueStateBridge


def _track_label(track: Track) -> str:
    title = track.title or track.filename
    return f"{track.artist} — {title}" if track.artist else title


class QueueSidebar(QWidget):
    playAtRequested = Signal(int)  # queue index the user wants to hear now

    def __init__(
        self,
        queue: QueueState,
        bridge: QueueStateBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._queue = queue

        self.now_playing_title = QLabel("Nothing playing")
        self.now_playing_title.setWordWrap(True)
        self.queue_list = QListWidget()

        self.up_button = QPushButton("Up")
        self.down_button = QPushButton("Down")
        self.play_next_button = QPushButton("Play next")
        self.remove_button = QPushButton("Remove")
        self.clear_button = QPushButton("Clear")
        self.shuffle_button = QPushButton("Shuffle")
        self.shuffle_button.setCheckable(True)
        self.repeat_button = QPushButton("Repeat: off")

        move_row = QHBoxLayout()
        move_row.addWidget(self.up_button)
        move_row.addWidget(self.down_button)
        move_row.addWidget(self.play_next_button)

        edit_row = QHBoxLayout()
        edit_row.addWidget(self.remove_button)
        edit_row.addWidget(self.clear_button)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.shuffle_button)
        mode_row.addWidget(self.repeat_button)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Now playing"))
        layout.addWidget(self.now_playing_title)
        layout.addWidget(QLabel("Queue"))
        layout.addWidget(self.queue_list, stretch=1)
        layout.addLayout(move_row)
        layout.addLayout(edit_row)
        layout.addLayout(mode_row)

        bridge.queueChanged.connect(self._render)
        bridge.currentChanged.connect(self._render)

        self.queue_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button.clicked.connect(lambda: self._move_selected(+1))
        self.play_next_button.clicked.connect(self._on_play_next)
        self.remove_button.clicked.connect(self._on_remove)
        self.clear_button.clicked.connect(self._queue.clear)
        self.shuffle_button.toggled.connect(self._queue.set_shuffle)
        self.repeat_button.clicked.connect(self._on_repeat)

        self._render()

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    def _render(self, *_args) -> None:
        current = self._queue.current_track
        self.now_playing_title.setText(_track_label(current) if current else "Nothing playing")

        selected = self.queue_list.currentRow()
        self.queue_list.clear()
        current_index = self._queue.current_index
        for i, track in enumerate(self._queue.tracks):
            prefix = "▶  " if i == current_index else ""
            item = QListWidgetItem(f"{prefix}{_track_label(track)}")
            if i == current_index:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.queue_list.addItem(item)
        if 0 <= selected < self.queue_list.count():
            self.queue_list.setCurrentRow(selected)

    # ------------------------------------------------------------------
    # slots
    # ------------------------------------------------------------------

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        self.playAtRequested.emit(self.queue_list.row(item))

    def _move_selected(self, delta: int) -> None:
        row = self.queue_list.currentRow()
        if row < 0:
            return
        self._queue.move(row, row + delta)
        target = row + delta
        if 0 <= target < self.queue_list.count():
            self.queue_list.setCurrentRow(target)

    def _on_play_next(self) -> None:
        row = self.queue_list.currentRow()
        if row >= 0:
            self._queue.play_next(row)

    def _on_remove(self) -> None:
        row = self.queue_list.currentRow()
        if row >= 0:
            self._queue.remove(row)

    def _on_repeat(self) -> None:
        mode = self._queue.cycle_repeat()
        self.repeat_button.setText(f"Repeat: {mode}")
