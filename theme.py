"""ORBIT visual identity; storage and service identifiers are managed separately."""
from pathlib import Path

APP_NAME = "星序"
APP_WORDMARK = "ORBIT"
APP_DISPLAY_NAME = "星序 / ORBIT"
TAGLINE = "让每一步，连成方向。"

PALETTE = {
    "canvas": "#f5f7fa", "surface": "#ffffff", "ink": "#273d52",
    "muted": "#5f7488", "primary": "#426c83", "line": "#dbe3ed",
    "selection": "#e7eff5", "accent": "#c89e66", "guide": "#cdd9e3",
}
CATEGORY_COLORS = {"work": "#47758d", "learn": "#8676a5", "demo": "#a5824d", "life": "#a4787d"}

STYLE = """
QWidget { font-family: 'Noto Sans CJK SC', 'Noto Sans', sans-serif; font-size: 13px; color: #273d52; }
QMainWindow, QDialog { background: #f5f7fa; }
QFrame#shell { background: #f5f7fa; border: 1px solid #dbe3ed; border-radius: 15px; }
QFrame#titlebar { background: #edf2f7; border-top-left-radius: 15px; border-top-right-radius: 15px; border-bottom: 1px solid #dbe3ed; }
QLabel { background: transparent; }
QLabel#muted { color: #5f7488; font-size: 12px; }
QLabel#eyebrow { color: #5f7488; font-size: 11px; letter-spacing: 1px; }
QLabel#h1 { font-size: 27px; font-weight: 600; color: #273d52; }
QLabel#h2 { font-size: 17px; font-weight: 600; }
QLabel#detailTitle { font-size: 20px; font-weight: 600; line-height: 1.5; }
QLabel#section { font-size: 12px; font-weight: 500; color: #5f7488; }
QLabel#brand { font-size: 20px; font-weight: 600; letter-spacing: 1px; }
QLabel#timer { font-family: 'Noto Sans', sans-serif; font-size: 38px; font-weight: 500; letter-spacing: 1px; color: #34576e; }
QLabel#miniTimer { font-family: 'Noto Sans', sans-serif; font-size: 64px; font-weight: 500; color: #34576e; }
QLabel#badge { background: #edf2f7; color: #5f7488; border-radius: 7px; padding: 3px 7px; font-size: 11px; }
QFrame#sidebar { background: #edf2f7; border-right: 1px solid #dbe3ed; border-bottom-left-radius: 15px; }
QFrame#details { background: #ffffff; border-left: 1px solid #dbe3ed; border-bottom-right-radius: 15px; }
QFrame#tutorPanel { background: #ffffff; border: 1px solid #dbe3ed; border-radius: 12px; }
QFrame#knowledgePanel { background: #e9f0f4; border: 1px solid #cdd9e3; border-radius: 12px; }
QTextBrowser#knowledgeText { background: #e9f0f4; border: 0; padding: 3px; font-size: 13px; }
QSplitter::handle:vertical { background: #edf2f7; }
QTextBrowser#tutorTranscript { background: #ffffff; border: 0; padding: 4px; font-size: 13px; }
QPlainTextEdit#tutorQuestion { background: #f5f7fa; }
QFrame#focus { background: #e9f0f4; border: 1px solid #dbe3ed; border-radius: 16px; }
QFrame#focusDivider { background: #d4e0e9; }
QFrame#line { background: #dbe3ed; max-height: 1px; min-height: 1px; }
QFrame#quiet { background: #e9f0f4; border-radius: 12px; }
QFrame#binding { background: #edf2f7; border: 1px solid #dbe3ed; border-radius: 12px; }
QFrame#taskRow { background: #ffffff; border: 1px solid #dbe3ed; border-radius: 12px; }
QFrame#taskRow[substep="true"] { background: #edf2f6; border-color: #e1e7ef; border-radius: 9px; }
QFrame#taskRow[context="true"] { background: #f5f7fa; border: 1px dashed #cdd9e3; }
QFrame#taskRow:hover { background: #f0f3f7; border-color: #cdd9e3; }
QFrame#taskRow[selected="true"] { background: #e7eff5; border: 1px solid #92adc1; }
QFrame#taskRow[complete="true"] { background: #f0f3f7; }
QFrame#stepBranch { background: transparent; border: 0; }
QFrame#stepRail { background: #cdd9e3; border: 0; border-radius: 1px; }
QLabel#substepBadge { color: #426c83; font-size: 11px; font-weight: 500; }
QLabel#contextBadge { color: #7d8b9b; font-size: 11px; }
QLabel#contextTitle { color: #5f7488; font-size: 12px; }
QLabel#dependencyHint { color: #8676a5; font-size: 11px; }
QPushButton { border: 0; background: transparent; border-radius: 8px; padding: 7px 11px; }
QPushButton:hover { background: #e7eff5; }
QPushButton:pressed { background: #d4e0e9; }
QPushButton:focus { outline: none; border: 1px solid #92adc1; }
QPushButton#primary { background: #426c83; color: #ffffff; border-radius: 9px; font-weight: 500; padding: 9px 15px; }
QPushButton#primary:hover { background: #34576e; }
QPushButton#coral { background: #e7c697; color: #514738; border-radius: 10px; font-weight: 500; padding: 10px 15px; }
QPushButton#coral:hover { background: #ddba87; }
QPushButton#outline { border: 1px solid #cdd9e3; background: #ffffff; border-radius: 8px; }
QPushButton#soft { background: #edf2f7; color: #5f7488; }
QPushButton#linkButton { color: #5f7488; text-align: left; padding: 3px 0; font-size: 12px; }
QPushButton#stepToggle { background: #edf2f7; color: #426c83; text-align: left; padding: 6px 8px; font-size: 11px; border-radius: 6px; }
QPushButton#stepToggle:hover { background: #dce8f0; }
QPushButton#stepToggle[expanded="true"] { background: #e1eaf1; }
QPushButton#deleteButton { color: #af766e; font-size: 12px; padding: 4px 8px; }
QPushButton#deleteButton:hover { background: #f5e9e5; }
QPushButton:disabled { color: #aebbc7; }
QPushButton#nav { text-align: left; padding: 9px 13px; font-size: 13px; color: #5f7488; border-radius: 10px; }
QPushButton#nav:checked { background: #dce8f0; color: #34576e; font-weight: 500; }
QPushButton#nav:hover { background: #e7eff5; }
QPushButton#filter { color: #5f7488; padding: 6px 11px; font-size: 12px; }
QPushButton#filter:checked { background: #e7eff5; color: #34576e; }
QPushButton#iconButton { padding: 6px; border-radius: 7px; }
QPushButton#statusDot { padding: 0; border: 1.5px solid #b5c7d5; border-radius: 9px; background: transparent; }
QPushButton#statusDot[status="doing"] { border-color: #426c83; background: #dce8f0; }
QPushButton#statusDot[status="waiting"] { border-color: #ad884e; background: #f4ead9; }
QPushButton#statusDot[status="done"] { border-color: #b5c7d5; background: #b5c7d5; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox { background: #f5f7fa; border: 1px solid #dbe3ed; border-radius: 8px; padding: 7px 9px; selection-background-color: #d4e0e9; }
QListWidget { background: #ffffff; border: 1px solid #dbe3ed; border-radius: 10px; padding: 8px; }
QListWidget::item { padding: 12px 7px; border-radius: 7px; }
QListWidget::item:selected { background: #dce8f0; color: #34576e; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #edf2f7; padding: 9px 12px; border-radius: 7px; margin: 0 5px 7px 0; }
QTabBar::tab:selected { background: #d4e0e9; color: #34576e; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QDateEdit:focus { border-color: #92adc1; }
QLineEdit#search { border: 1px solid #dbe3ed; background: #f0f3f7; font-size: 12px; padding: 8px; }
QLineEdit#nextStep { background: #edf2f7; border-color: #dbe3ed; padding: 9px; }
QTextEdit#note { background: #f9f7f1; border: 1px solid #e7e1d4; color: #5f7488; font-size: 12px; }
QComboBox { padding-right: 24px; }
QComboBox::drop-down { border: 0; width: 20px; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #dce8f0; selection-color: #273d52; border: 1px solid #cdd9e3; }
QCheckBox { spacing: 9px; background: transparent; padding: 3px 0; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #b5c7d5; border-radius: 5px; background: #ffffff; }
QCheckBox::indicator:checked { background: #92adc1; border-color: #92adc1; image: none; }
QCheckBox:checked { color: #7d8b9b; }
QProgressBar { background: #d4e0e9; border: 0; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #92adc1; border-radius: 3px; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 5px; margin: 0; }
QScrollBar::handle:vertical { background: #d4e0e9; border-radius: 2px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #34576e; color: #ffffff; border: 0; padding: 6px; }
"""
STYLE += 'QCheckBox::indicator:checked { image: url("' + (Path(__file__).parent / "assets" / "check.svg").as_posix() + '"); }'
