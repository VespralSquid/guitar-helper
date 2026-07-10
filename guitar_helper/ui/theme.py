"""All styling constants. Nothing here holds behavior — widgets read this
module and never hardcode a color, font, size, or QSS string of their own.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QIcon

TONE_COLORS: dict[str, str] = {
    "clean": "#3b82f6",
    "edge": "#22c55e",
    "overdrive": "#eab308",
    "crunch": "#f97316",
    "metal": "#ef4444",
    "other": "#6b7280",
}

FONT_FAMILY = "Segoe UI"
ICON_DIR = Path(__file__).parent / "assets" / "icons"

WAVEFORM_COLUMNS = 3000
BAND_ALPHA = 70
BAND_ALPHA_SELECTED = 160
PLAYHEAD_WIDTH = 2
PLAYHEAD_COLOR = "#e5e7eb"
WAVEFORM_COLOR = "#93c5fd"


def tone_color(label: str) -> str:
    return TONE_COLORS.get(label, TONE_COLORS["other"])


def tone_brush(label: str, *, selected: bool = False):
    from pyqtgraph import mkBrush

    color = QColor(tone_color(label))
    color.setAlpha(BAND_ALPHA_SELECTED if selected else BAND_ALPHA)
    return mkBrush(color)


def tone_pen(label: str):
    from pyqtgraph import mkPen

    return mkPen(QColor(tone_color(label)), width=1)


def icon(name: str) -> QIcon | None:
    """Return a QIcon for `name` if an asset exists, else None — callers fall
    back to a plain text label so the app runs with zero binary assets."""
    path = ICON_DIR / f"{name}.png"
    return QIcon(str(path)) if path.exists() else None


APP_QSS = """
QMainWindow, QWidget {
    background-color: #1e1e24;
    color: #e5e7eb;
    font-family: "Segoe UI";
}
QListWidget, QTableView {
    background-color: #26262f;
    border: 1px solid #3a3a45;
    gridline-color: #3a3a45;
}
QHeaderView::section {
    background-color: #2e2e38;
    color: #e5e7eb;
    border: none;
    padding: 4px;
}
QPushButton {
    background-color: #33333e;
    border: 1px solid #45454f;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton:hover {
    background-color: #3d3d48;
}
QPushButton:pressed {
    background-color: #2a2a33;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #3a3a45;
}
QSlider::handle:horizontal {
    background: #93c5fd;
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
}
QMenuBar, QMenu {
    background-color: #26262f;
    color: #e5e7eb;
}
QMenu::item:selected {
    background-color: #3d3d48;
}
"""
