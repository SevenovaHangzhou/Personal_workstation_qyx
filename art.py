"""Small vector illustrations and icons painted with Qt; no bitmap dependencies."""
from PyQt5.QtCore import Qt, QRectF, QPointF, QSize
from PyQt5.QtGui import QColor, QPainter, QPen, QPainterPath, QPixmap, QIcon
from PyQt5.QtWidgets import QWidget
from theme import PALETTE


ORBIT_POINTS = ((4, 18), (7, 12), (5, 6), (11, 3), (18, 5), (21, 12), (16, 18))


def draw_orbit(painter, color, accent=None):
    """Seven waypoints form an open trajectory; the last point is a small star."""
    painter.setPen(QPen(QColor(color), .95, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    path = QPainterPath(QPointF(*ORBIT_POINTS[0]))
    for point in ORBIT_POINTS[1:]:
        path.lineTo(QPointF(*point))
    painter.drawPath(path)
    painter.setBrush(QColor(PALETTE["surface"]))
    for x, y in ORBIT_POINTS[:-1]:
        painter.drawEllipse(QRectF(x - .95, y - .95, 1.9, 1.9))
    x, y = ORBIT_POINTS[-1]
    star = QPainterPath(QPointF(x, y - 2.25))
    for dx, dy in [(.65, -.65), (2.25, 0), (.65, .65), (0, 2.25), (-.65, .65), (-2.25, 0), (-.65, -.65)]:
        star.lineTo(x + dx, y + dy)
    star.closeSubpath()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(accent or color))
    painter.drawPath(star)


def icon(name, color=PALETTE["muted"], size=20):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    def line(x1, y1, x2, y2): painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    def rect(x, y, w, h, r=3): painter.drawRoundedRect(QRectF(x, y, w, h), r, r)
    if name == "orbit":
        draw_orbit(painter, color)
    elif name == "sun":
        painter.drawEllipse(QRectF(7, 7, 10, 10))
        for points in [(12,2,12,4),(12,20,12,22),(2,12,4,12),(20,12,22,12),(5,5,6.5,6.5),(17.5,17.5,19,19),(5,19,6.5,17.5),(17.5,6.5,19,5)]: line(*points)
    elif name in ("tasks", "work"):
        if name == "work":
            rect(3,7,18,14); rect(8,3,8,4,1); line(3,12,21,12); line(10,12,14,12)
        else:
            rect(4,3,16,19); line(8,8,16,8); line(8,12,16,12); line(8,16,13,16)
    elif name == "book":
        path=QPainterPath(QPointF(12,6)); path.cubicTo(8,3,4,4,3,5); path.lineTo(3,19); path.cubicTo(7,17,9,18,12,20); path.cubicTo(16,18,18,17,21,19); path.lineTo(21,5); path.cubicTo(17,3,15,4,12,6); painter.drawPath(path); line(12,6,12,20)
    elif name == "flask":
        line(8,3,16,3); path=QPainterPath(QPointF(9,3)); path.lineTo(9,10); path.lineTo(4,18); path.quadTo(3,21,6,21); path.lineTo(18,21); path.quadTo(21,21,20,18); path.lineTo(15,10); path.lineTo(15,3); painter.drawPath(path); line(7,15,17,15)
    elif name == "cup":
        rect(4,7,13,13,3); painter.drawArc(QRectF(14,8,8,8),-90*16,180*16); line(7,3,7,4); line(12,2,12,4)
    elif name in ("link", "external"):
        if name == "external":
            path=QPainterPath(QPointF(11,4)); path.lineTo(5,4); path.quadTo(3,4,3,6); path.lineTo(3,19); path.quadTo(3,21,5,21); path.lineTo(18,21); path.quadTo(20,21,20,19); path.lineTo(20,13); painter.drawPath(path); line(11,13,21,3); line(14,3,21,3); line(21,3,21,10)
        else:
            painter.translate(12,12); painter.rotate(-35); rect(-10,-4,12,8,4); rect(-2,-4,12,8,4); line(-4,0,4,0)
    elif name == "search":
        painter.drawEllipse(QRectF(4,3,12,12)); line(14,14,21,21)
    elif name == "plus": line(5,12,19,12); line(12,5,12,19)
    elif name == "check": line(5,12,10,17); line(10,17,20,6)
    elif name == "clock":
        painter.drawEllipse(QRectF(3,3,18,18)); line(12,7,12,12); line(12,12,16,14)
    elif name == "play":
        painter.setBrush(QColor(color)); path=QPainterPath(QPointF(8,5)); path.lineTo(19,12); path.lineTo(8,19); path.closeSubpath(); painter.drawPath(path)
    elif name == "pause": line(9,5,9,19); line(16,5,16,19)
    elif name == "reset":
        painter.drawArc(QRectF(4,4,16,16),-60*16,300*16); line(4,3,4,9); line(4,9,10,9)
    elif name == "leaf":
        path=QPainterPath(QPointF(5,19)); path.cubicTo(2,8,10,3,21,3); path.cubicTo(21,15,13,23,5,19); painter.drawPath(path); line(5,19,15,10)
    elif name == "codex":
        line(8,5,2,12); line(2,12,8,19); line(16,5,22,12); line(22,12,16,19); line(14,3,10,21)
    elif name == "linear":
        painter.drawEllipse(QRectF(3,3,18,18)); line(5,15,15,5); line(7,18,18,7); line(11,20,20,11)
    elif name == "grid":
        for x in [4,14]:
            for y in [4,14]: rect(x,y,6,6,1)
    elif name == "list":
        for y in [6,12,18]: line(4,y,5,y); line(9,y,20,y)
    elif name == "chevron": line(9,6,15,12); line(15,12,9,18)
    elif name == "close": line(6,6,18,18); line(6,18,18,6)
    elif name == "minimize": line(5,16,19,16)
    elif name == "maximize": rect(5,5,14,14,1)
    elif name == "settings":
        painter.drawEllipse(QRectF(4,4,16,16)); painter.drawEllipse(QRectF(9,9,6,6))
        for points in [(12,1,12,4),(12,20,12,23),(1,12,4,12),(20,12,23,12)]: line(*points)
    elif name == "more":
        painter.setBrush(QColor(color)); painter.setPen(Qt.NoPen)
        for x in [5,12,19]: painter.drawEllipse(QRectF(x-1,11,2,2))
    elif name == "sparkle":
        path=QPainterPath(QPointF(12,2));
        for x,y in [(15,9),(22,12),(15,15),(12,22),(9,15),(2,12),(9,9)]: path.lineTo(x,y)
        path.closeSubpath(); painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


class OrbitMark(QWidget):
    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self.compact = compact
        self.setFixedSize(42, 42) if compact else self.setFixedSize(118, 136)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAccessibleName("星序轨迹标记")

    def paintEvent(self, event):
        p=QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        if self.compact:
            p.translate(2, 2); p.scale(38 / 24, 38 / 24)
            draw_orbit(p, PALETTE["primary"], PALETTE["accent"])
        else:
            p.setPen(Qt.NoPen); p.setBrush(QColor("#e1eaf1")); p.drawEllipse(QRectF(7,18,104,104))
            p.setBrush(QColor("#f2f6f9")); p.drawEllipse(QRectF(22,33,74,74))
            p.setPen(QPen(QColor("#b8cad8"), .8))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(12,23,94,94), 26*16, 135*16)
            p.drawArc(QRectF(12,23,94,94), 206*16, 108*16)
            for x,y in [(8,20),(106,20),(8,120),(106,120)]:
                p.drawLine(QPointF(x-3,y),QPointF(x+3,y))
                p.drawLine(QPointF(x,y-3),QPointF(x,y+3))
            p.translate(11, 20); p.scale(4, 4)
            draw_orbit(p, PALETTE["primary"], PALETTE["accent"])
        p.end()


# Retain the old import for integrations that only embed the illustration.
Mascot = OrbitMark
