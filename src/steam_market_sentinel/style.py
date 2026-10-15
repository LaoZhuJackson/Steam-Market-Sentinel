"""One compact dark theme shared by all desktop views.

Palette roles — keep every view on these values:

    window #0f1823   panel #16222f   card #1b2a38   raised/header #223344
    border #2b3a4d   border-strong #38516a         selection #254b51
    text #e9eef6     text-muted #93a4b8            text-disabled #66788c
    accent #5fd7b4   accent-hover #7ce8c8          accent-ink #0b1f22
    danger #ff8f7a   warning #ffc27a

The stylesheet deliberately sets **no** universal ``QWidget { background: ... }``.
A Qt type selector also matches subclasses, so that rule used to paint an opaque
window-coloured rectangle behind every QLabel/QCheckBox — visible as a mismatched
dark box on top of the lighter panels. Unstyled containers are kept dark by the
QPalette applied in :func:`apply_theme` instead.
"""
from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QPalette

PALETTE = {
    "window": "#0f1823",
    "panel": "#16222f",
    "card": "#1b2a38",
    "raised": "#223344",
    "border": "#2b3a4d",
    "border_strong": "#38516a",
    "border_hover": "#4a6a86",
    "selection": "#254b51",
    "text": "#e9eef6",
    "text_strong": "#f2f7ff",
    "text_muted": "#93a4b8",
    "text_disabled": "#66788c",
    "accent": "#5fd7b4",
    "accent_hover": "#7ce8c8",
    "accent_active": "#4cc4a3",
    "accent_ink": "#0b1f22",
    "danger": "#ff8f7a",
    # 限流 is not an error the user can fix by waiting with a *network* view: it is a
    # rate limit, so it gets its own amber rather than the danger red.
    "warning": "#e8c07d",
    "danger_surface": "#2c1c1c",
    "danger_surface_hover": "#3a2321",
    "danger_border": "#6b3a34",
    "on_accent": "#ffffff",
}

_RAW = """
/* --- base ------------------------------------------------------------- */
QWidget {
    color: $text;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 12px;
}
QMainWindow, QDialog { background: $window; }

/* --- text roles ------------------------------------------------------- */
QLabel, QCheckBox, QRadioButton { background: transparent; }
QCheckBox { spacing: 8px; }
/* Explicitly styled: Fusion draws its unchecked indicator from colours that are
   invisible against these dark surfaces, and the palette does not affect it. */
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid $border_strong; background: $panel;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QCheckBox::indicator:disabled { background: $card; border-color: $border; }
QLabel#hero { font-size: 22px; font-weight: 700; color: $text_strong; }
QLabel#subtle { color: $text_muted; }
QLabel#section { font-size: 13px; font-weight: 600; color: #dce6f2; }
QLabel#metricCaption { color: $text_muted; font-size: 11px; }
QLabel#metricValue { font-size: 18px; font-weight: 700; color: $accent; }
QLabel#badge {
    color: $accent; background: #14333a; border: 1px solid #1f5a56;
    border-radius: 9px; padding: 1px 9px; font-size: 11px;
}
QLabel#hint {
    color: $text_muted; background: $raised; border-left: 3px solid $accent;
    border-radius: 6px; padding: 8px 10px;
}
QLabel#thumb {
    background: $card; border: 1px solid $border; border-radius: 10px;
    font-size: 30px;
}

/* --- containers ------------------------------------------------------- */
QFrame#panel { background: $panel; border: 1px solid $border; border-radius: 12px; }
QFrame#card, QFrame#stat {
    background: $card; border: 1px solid $border; border-radius: 10px;
}
QFrame#hline { background: $border; border: 0; }
/* Transparent so the framed panel inside keeps its own rounded surface. */
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: 0; }
QSplitter::handle { background: $window; width: 7px; }
QSplitter::handle:hover { background: $border; }

/* --- inputs ----------------------------------------------------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: $card; border: 1px solid $border_strong; border-radius: 7px;
    padding: 7px 9px; selection-background-color: $accent; selection-color: $accent_ink;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
    border: 1px solid $border_hover;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid $accent;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background: $panel; color: $text_disabled; border: 1px solid $border;
}
QComboBox::drop-down { border: 0; border-left: 1px solid $border; width: 22px; }
QComboBox QAbstractItemView {
    background: $card; color: $text; border: 1px solid $border_strong;
    border-radius: 6px; padding: 4px; outline: 0;
    selection-background-color: $selection; selection-color: $on_accent;
}

/* --- buttons ---------------------------------------------------------- */
QPushButton {
    background: $card; color: $text; border: 1px solid $border_strong;
    padding: 8px 14px; border-radius: 8px; font-weight: 500;
}
QPushButton:hover { background: $raised; border-color: $border_hover; }
QPushButton:pressed { background: $panel; }
QPushButton:disabled {
    color: $text_disabled; background: $panel; border-color: $border;
}
QPushButton#primary {
    color: $accent_ink; background: $accent; border: 1px solid $accent; font-weight: 700;
}
QPushButton#primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#primary:pressed { background: $accent_active; }
QPushButton#primary:disabled {
    color: $text_disabled; background: $panel; border-color: $border;
}
/* The per-row ✕ in the watchlist: readable at rest, clearly destructive on hover. */
QPushButton#rowRemove {
    color: $text_muted; background: transparent; border: 1px solid transparent;
    padding: 0; font-size: 13px; font-weight: 700;
}
QPushButton#rowRemove:hover {
    color: $danger; background: $danger_surface_hover; border: 1px solid $danger_border;
    border-radius: 6px;
}
QPushButton#danger {
    color: $danger; background: $danger_surface; border: 1px solid $danger_border;
}
QPushButton#danger:hover {
    background: $danger_surface_hover; border-color: $danger;
}

/* --- tables ----------------------------------------------------------- */
QTableWidget {
    background: $panel; alternate-background-color: $card;
    border: 1px solid $border; border-radius: 9px; gridline-color: transparent;
    selection-background-color: $selection;
}
QTableWidget::item { padding: 6px 9px; border: 0; }
QTableWidget::item:selected { background: $selection; color: $on_accent; }
QHeaderView { background: transparent; border: 0; }
QHeaderView::section {
    background: $raised; color: #9fb3c8; padding: 9px; border: 0;
    border-bottom: 1px solid $border; font-weight: 600;
}
QHeaderView::section:first { border-top-left-radius: 8px; }
QHeaderView::section:last { border-top-right-radius: 8px; }
QTableCornerButton::section { background: $raised; border: 0; }

/* --- tabs ------------------------------------------------------------- */
QTabWidget::pane {
    background: $panel; border: 1px solid $border; border-radius: 9px;
}
QTabBar { background: transparent; }
QTabBar::tab {
    background: $card; color: $text_muted; padding: 9px 16px;
    margin-right: 4px; border: 1px solid $border; border-radius: 7px;
}
QTabBar::tab:hover { color: $text; border-color: $border_strong; }
QTabBar::tab:selected {
    background: $selection; color: $on_accent; border-color: $selection;
}

/* --- menus ------------------------------------------------------------ */
QMenu { background: $panel; border: 1px solid $border; border-radius: 9px; padding: 6px; }
QMenu::item { padding: 8px 22px; border-radius: 6px; color: $text; }
QMenu::item:selected { background: $selection; color: $on_accent; }
QMenu::separator { height: 1px; background: $border; margin: 5px 9px; }

/* --- status bar ------------------------------------------------------- */
QStatusBar { background: $window; color: $text_muted; border-top: 1px solid $border; }
QStatusBar::item { border: 0; }

/* --- charts ----------------------------------------------------------- */
QChartView { background: transparent; border: 1px solid $border; border-radius: 9px; }

/* --- scrollbars ------------------------------------------------------- */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #45556c; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:horizontal { background: #45556c; border-radius: 5px; min-width: 28px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #5a6d86; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* --- tooltips --------------------------------------------------------- */
QToolTip {
    color: $on_accent; background: $raised; border: 1px solid $border_strong;
    border-radius: 5px; padding: 5px 7px;
}
"""

STYLE = Template(_RAW).substitute(PALETTE)


def _palette() -> QPalette:
    """Dark palette so widgets the stylesheet does not name — spin/combo arrows,
    checkbox indicators, popup views, tab pages — stay dark without a global
    ``QWidget`` background rule."""
    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    palette = QPalette()
    surface = {
        role.Window: PALETTE["window"],
        role.WindowText: PALETTE["text"],
        role.Base: PALETTE["panel"],
        role.AlternateBase: PALETTE["card"],
        role.Text: PALETTE["text"],
        role.Button: PALETTE["card"],
        role.ButtonText: PALETTE["text"],
        role.BrightText: PALETTE["danger"],
        role.Highlight: PALETTE["selection"],
        role.HighlightedText: PALETTE["on_accent"],
        role.ToolTipBase: PALETTE["raised"],
        role.ToolTipText: PALETTE["on_accent"],
        role.Link: PALETTE["accent"],
        role.PlaceholderText: PALETTE["text_disabled"],
    }
    # Both Active and Inactive must be filled in: QPalette starts from the
    # style's light defaults, so leaving Inactive unset would turn an unfocused
    # window light again.
    for state in (group.Active, group.Inactive, group.Disabled):
        for color_role, value in surface.items():
            palette.setColor(state, color_role, QColor(value))
    for disabled_role in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(group.Disabled, disabled_role, QColor(PALETTE["text_disabled"]))
    return palette


def apply_theme(app) -> None:
    """Apply the shared dark theme to a QApplication.

    Fusion + an explicit dark palette keep every widget that the stylesheet does
    not name (spin/combo arrows, popup views) dark and consistent. Combo and
    spin arrows are intentionally left to the style so the real arrow glyphs are
    kept; checkbox indicators must be styled explicitly (see above) because
    Fusion's unchecked box is invisible on these dark surfaces.

    Order matters — ``setStyle`` replaces the application palette, so the
    palette must be set *after* the style and *before* the stylesheet.
    """
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setStyleSheet(STYLE)
