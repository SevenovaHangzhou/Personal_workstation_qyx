"""Shared native Qt widgets and layout helpers."""
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtWidgets import QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QFrame
from art import icon
from theme import PALETTE


def label(text, name=None, wrap=False):
    w = QLabel(text)
    w.setTextFormat(Qt.PlainText)
    if name: w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text="", name=None, glyph=None, color=PALETTE["muted"], callback=None):
    w = QPushButton(text)
    if name: w.setObjectName(name)
    if glyph:
        w.setIcon(icon(glyph, color, 18)); w.setIconSize(QSize(18,18))
    w.setCursor(Qt.PointingHandCursor)
    if callback: w.clicked.connect(callback)
    return w


def box(parent=None, horizontal=False, margins=(0,0,0,0), spacing=10):
    layout = QHBoxLayout(parent) if horizontal else QVBoxLayout(parent)
    layout.setContentsMargins(*margins); layout.setSpacing(spacing)
    return layout


def line():
    w=QFrame(); w.setObjectName("line"); return w


def clear(layout):
    while layout.count():
        item=layout.takeAt(0)
        if item.widget(): item.widget().deleteLater()
        elif item.layout(): clear(item.layout())


