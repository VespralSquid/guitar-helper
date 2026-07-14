"""Placeholder pages so navigation is complete before O4 lands."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class _PlaceholderMode(QWidget):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addWidget(label)


class OutputMode(_PlaceholderMode):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Output — MIDI configuration and dispatch log arrive in milestone O4.", parent)
