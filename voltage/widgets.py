"""On-screen keyboard and oscilloscope."""
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget

BLACK = {1, 3, 6, 8, 10}


class Piano(QWidget):
    """Mouse-playable keyboard; also lights up notes played from MIDI or the computer keys."""

    def __init__(self, engine, low=36, high=96):
        super().__init__()
        self.engine, self.low, self.high = engine, low, high
        self.whites = [n for n in range(low, high + 1) if n % 12 not in BLACK]
        self.mouse_note = None
        self.setMinimumSize(500, 96)
        timer = QTimer(self)
        timer.timeout.connect(self.update)
        timer.start(40)

    def white_width(self):
        return self.width() / len(self.whites)

    def black_rect(self, note):
        ww = self.white_width()
        left = self.whites.index(note - 1)
        return QRectF((left + 1) * ww - ww * 0.3, 0, ww * 0.6, self.height() * 0.62)

    def note_at(self, pos):
        if not self.rect().contains(pos.toPoint()):
            return None
        for n in range(self.low, self.high + 1):
            if n % 12 in BLACK and self.black_rect(n).contains(pos):
                return n
        i = min(len(self.whites) - 1, max(0, int(pos.x() / self.white_width())))
        return self.whites[i]

    def play(self, note):
        if note == self.mouse_note:
            return
        if self.mouse_note is not None:
            self.engine.note_off(self.mouse_note)
        self.mouse_note = note
        if note is not None:
            self.engine.note_on(note, 0.8)

    def mousePressEvent(self, ev):
        self.play(self.note_at(ev.position()))

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton:
            self.play(self.note_at(ev.position()))

    def mouseReleaseEvent(self, ev):
        self.play(None)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ww, h, held = self.white_width(), self.height(), self.engine.active
        for i, n in enumerate(self.whites):
            r = QRectF(i * ww, 0, ww - 1, h - 1)
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0, QColor("#e8b923") if n in held else QColor("#d9d5cb"))
            g.setColorAt(1, QColor("#f2c94c") if n in held else QColor("#fbf8f0"))
            p.setPen(QPen(QColor("#3b3b3b"), 1))
            p.setBrush(g)
            p.drawRoundedRect(r, 2, 2)
            if n % 12 == 0:
                p.setPen(QColor("#8a867d"))
                p.drawText(r.adjusted(0, 0, 0, -4), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                           f"C{n // 12 - 1}")
        for n in range(self.low, self.high + 1):
            if n % 12 in BLACK:
                p.setPen(QPen(QColor("#000"), 1))
                p.setBrush(QColor("#c8941a") if n in held else QColor("#161616"))
                p.drawRoundedRect(self.black_rect(n), 2, 2)


class Scope(QWidget):
    """Oscilloscope with a rising-edge trigger and a peak meter down the right side."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.level = 0.0
        self.setFixedSize(230, 96)
        timer = QTimer(self)
        timer.timeout.connect(self.update)
        timer.start(33)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() - 14, self.height()
        p.fillRect(self.rect(), QColor("#07110b"))
        p.setPen(QPen(QColor("#163322"), 1))
        for i in range(1, 4):
            p.drawLine(QPointF(w * i / 4, 0), QPointF(w * i / 4, h))
        p.drawLine(QPointF(0, h / 2), QPointF(w, h / 2))
        data = self.engine.scope(1536)
        start = 0
        for i in range(1, 768):
            if data[i - 1] <= 0 < data[i]:
                start = i
                break
        n = 768
        gain = h * 0.46 / max(0.25, max(abs(data[start + i]) for i in range(0, n, 4)))
        pts = QPolygonF([QPointF(w * i / n, h / 2 - data[start + i] * gain) for i in range(0, n, 2)])
        p.setPen(QPen(QColor("#52e58a"), 1.4))
        p.drawPolyline(pts)
        self.level = max(self.engine.peak(), self.level * 0.85)
        bar = min(1.0, self.level) * h
        p.fillRect(QRectF(w + 3, 0, 11, h), QColor("#111"))
        p.fillRect(QRectF(w + 3, h - bar, 11, bar), QColor("#d8362f") if self.level > 0.95 else QColor("#52e58a"))
