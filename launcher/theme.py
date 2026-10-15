"""Cyberpunk palette and stylesheet for the Qwen-Image-2.1 launcher.

Blue / black / green / fluorescent, as requested. Kept in one module so the whole
UI restyles from here.
"""

# ---------------------------------------------------------------- palette
BG        = "#05070d"   # near-black with a blue cast
PANEL     = "#0a0f1a"
PANEL_ALT = "#0d1626"
BORDER    = "#123044"
CYAN      = "#00e5ff"   # primary fluorescent blue
CYAN_DIM  = "#0091a8"
GREEN     = "#39ff88"   # fluorescent green (ok / ready)
GREEN_DIM = "#1f8f4d"
MAGENTA   = "#ff2bd6"   # accent for alerts and focus
AMBER     = "#ffc857"   # warnings
RED       = "#ff4d6d"   # errors
TEXT      = "#cfe9f5"
TEXT_DIM  = "#5d7f92"
TEXT_MUTE = "#33566b"

FONT_MONO = "Consolas, 'Cascadia Mono', 'DejaVu Sans Mono', monospace"
FONT_UI   = "'Segoe UI', 'Microsoft YaHei UI', sans-serif"

# ---------------------------------------------------------------- stylesheet
QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: {FONT_UI};
    font-size: 15px;
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* ---- headings -------------------------------------------------------- */
QLabel#Title {{
    color: {CYAN};
    font-family: {FONT_MONO};
    font-size: 32px;
    font-weight: bold;
    letter-spacing: 4px;
}}
QLabel#Subtitle {{ color: {TEXT_DIM}; font-family: {FONT_MONO}; font-size: 14px; }}
QLabel#Section {{
    color: {GREEN};
    font-family: {FONT_MONO};
    font-size: 15px;
    font-weight: bold;
    letter-spacing: 2px;
    padding: 6px 0 2px 0;
}}
QLabel#Hint {{ color: {TEXT_DIM}; font-size: 14px; }}
QLabel#Mono  {{ font-family: {FONT_MONO}; color: {TEXT}; }}

/* ---- frames ---------------------------------------------------------- */
QFrame#Card {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
}}
QFrame#Card[active="true"] {{
    border: 1px solid {CYAN_DIM};
    background: {PANEL_ALT};
}}
QFrame#Rule {{ background: {BORDER}; max-height: 1px; border: none; }}

/* ---- buttons --------------------------------------------------------- */
QPushButton {{
    background: {PANEL_ALT};
    color: {CYAN};
    border: 1px solid {CYAN_DIM};
    border-radius: 2px;
    padding: 8px 18px;
    font-family: {FONT_MONO};
    letter-spacing: 1px;
}}
QPushButton:hover  {{ background: #10263a; border-color: {CYAN}; color: #ffffff; }}
QPushButton:pressed{{ background: #071726; }}
QPushButton:disabled {{
    color: {TEXT_MUTE};
    border-color: #16303f;
    background: #070c14;
}}
QPushButton#Primary {{
    background: {CYAN_DIM};
    color: #02121a;
    border: 1px solid {CYAN};
    font-weight: bold;
}}
QPushButton#Primary:hover {{ background: {CYAN}; color: #001018; }}
QPushButton#Primary:disabled {{ background: #10303c; color: {TEXT_MUTE}; border-color: #16303f; }}
QPushButton#Danger {{ color: {RED}; border-color: #5c1f2c; }}
QPushButton#Danger:hover {{ background: #2a0d14; border-color: {RED}; color: #fff; }}

/* ---- list / table ---------------------------------------------------- */
QListWidget, QTableWidget, QTreeWidget {{
    background: {PANEL};
    border: 1px solid {BORDER};
    gridline-color: #10222f;
    font-family: {FONT_MONO};
    selection-background-color: #10384d;
    selection-color: {CYAN};
}}
QListWidget::item {{ padding: 9px 10px; border-bottom: 1px solid #0b1520; }}
QListWidget::item:selected {{ background: #10384d; color: {CYAN}; }}
QHeaderView::section {{
    background: {PANEL_ALT};
    color: {GREEN};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    font-family: {FONT_MONO};
}}
QTableWidget {{ selection-background-color: #10384d; }}

/* ---- inputs ---------------------------------------------------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: #060b13;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 2px;
    padding: 6px 8px;
    font-family: {FONT_MONO};
    selection-background-color: {CYAN_DIM};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border: 1px solid {CYAN};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {PANEL};
    border: 1px solid {CYAN_DIM};
    selection-background-color: #10384d;
    color: {TEXT};
}}

/* ---- checks / radios ------------------------------------------------- */
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {CYAN_DIM};
    background: #060b13;
}}
QCheckBox::indicator:checked {{ background: {GREEN}; border-color: {GREEN}; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QRadioButton::indicator:checked {{ background: {CYAN}; border-color: {CYAN}; }}

/* ---- progress / scroll ----------------------------------------------- */
QProgressBar {{
    background: #060b13;
    border: 1px solid {BORDER};
    border-radius: 2px;
    height: 16px;
    text-align: center;
    color: {TEXT};
    font-family: {FONT_MONO};
    font-size: 13px;
}}
QProgressBar::chunk {{ background: {GREEN}; }}
QScrollBar:vertical {{ background: {PANEL}; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {CYAN_DIM}; min-height: 26px; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: {CYAN}; }}
QScrollBar:horizontal {{ background: {PANEL}; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {CYAN_DIM}; min-width: 26px; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- console --------------------------------------------------------- */
QPlainTextEdit#Console, QTextEdit#Console {{
    background: #03060b;
    color: {GREEN};
    border: 1px solid {BORDER};
    font-family: {FONT_MONO};
    font-size: 12px;
}}

/* ---- step rail ------------------------------------------------------- */
QLabel#Step {{
    font-family: {FONT_MONO};
    font-size: 15px;
    color: {TEXT_MUTE};
    padding: 8px 12px;
    border-left: 3px solid transparent;
}}
QLabel#Step[state="done"]   {{ color: {GREEN_DIM}; border-left-color: {GREEN_DIM}; }}
QLabel#Step[state="active"] {{ color: {CYAN}; border-left-color: {CYAN}; font-weight: bold; }}
QLabel#Step[state="todo"]   {{ color: {TEXT_MUTE}; }}

QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; font-family: {FONT_MONO}; }}
QToolTip {{
    background: {PANEL_ALT};
    color: {TEXT};
    border: 1px solid {CYAN_DIM};
    padding: 4px;
}}
"""
