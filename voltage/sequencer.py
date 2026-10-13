"""Pattern sequencer panel: transport plus a 16-step note grid.

The pattern plays the rack exactly as the keyboard would, timed inside the
audio engine. State lives here as plain data and is pushed to the engine on
every edit.
"""
import random

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget

from .engine import G_BPM, G_GATE, G_SEQLEN, G_SWING

STEPS = 16
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
DEFAULT_NOTES = [45, 45, 57, 45, 48, 45, 55, 57, 45, 45, 57, 45, 52, 50, 48, 43]
DEFAULT_OFF = {3, 9}
DEFAULT_ACCENT = {0, 6, 12}
DEFAULT_SLIDE = {6, 13}


def note_name(note):
    return f"{NAMES[note % 12]}{note // 12 - 1}"


def default_steps():
    return [{"note": n, "on": i not in DEFAULT_OFF, "accent": i in DEFAULT_ACCENT, "slide": i in DEFAULT_SLIDE}
            for i, n in enumerate(DEFAULT_NOTES)]


class StepGrid(QWidget):
    """One cell per 16th note. Click the note to switch the step on/off, drag it
    up/down (or use the wheel) to change pitch; ACC and SLIDE toggle below."""

    FLAG_H = 18

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.playhead = -1
        self.drag = None
        self.setMinimumHeight(76)
        self.setToolTip("Click: step on/off · drag up/down or wheel: pitch (Shift = octaves) · "
                        "ACC: accent · SLIDE: tie into the next step with glide")

    def cell_width(self):
        return self.width() / STEPS

    def index_at(self, x):
        return max(0, min(STEPS - 1, int(x / self.cell_width())))

    def mousePressEvent(self, ev):
        i = self.index_at(ev.position().x())
        step = self.panel.steps[i]
        if ev.position().y() >= self.height() - self.FLAG_H:
            left = ev.position().x() - i * self.cell_width() < self.cell_width() / 2
            key = "accent" if left else "slide"
            step[key] = not step[key]
            self.panel.push(i)
        else:
            self.drag = {"i": i, "y": ev.position().y(), "note": step["note"], "moved": False}

    def mouseMoveEvent(self, ev):
        if not self.drag:
            return
        d = self.drag
        delta = int((d["y"] - ev.position().y()) / 5)
        if delta:
            d["moved"] = True
        note = max(12, min(108, d["note"] + delta))
        if note != self.panel.steps[d["i"]]["note"]:
            self.panel.steps[d["i"]]["note"] = note
            self.panel.steps[d["i"]]["on"] = True
            self.panel.push(d["i"])

    def mouseReleaseEvent(self, ev):
        if self.drag and not self.drag["moved"]:
            step = self.panel.steps[self.drag["i"]]
            step["on"] = not step["on"]
            self.panel.push(self.drag["i"])
        self.drag = None

    def wheelEvent(self, ev):
        i = self.index_at(ev.position().x())
        step = self.panel.steps[i]
        amount = 12 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        step["note"] = max(12, min(108, step["note"] + (amount if ev.angleDelta().y() > 0 else -amount)))
        self.panel.push(i)
        ev.accept()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cw, h, fh = self.cell_width(), self.height(), self.FLAG_H
        big, small = QFont("DejaVu Sans"), QFont("DejaVu Sans")
        big.setPixelSize(13)
        big.setBold(True)
        small.setPixelSize(8)
        small.setBold(True)
        length = self.panel.length.value()
        for i, step in enumerate(self.panel.steps):
            x = i * cw
            active = i < length
            cell = QRectF(x + 2, 1, cw - 4, h - fh - 4)
            if i == self.playhead:
                fill = QColor("#e8b923")
            elif step["on"] and active:
                fill = QColor("#3a2c1f") if i % 4 else QColor("#4a3826")
            else:
                fill = QColor("#1c1612")
            p.setPen(QPen(QColor("#e8b923") if i % 4 == 0 and active else QColor("#5a4636"), 1))
            p.setBrush(fill)
            p.drawRoundedRect(cell, 3, 3)
            p.setFont(big)
            if i == self.playhead:
                p.setPen(QColor("#111"))
            elif step["on"] and active:
                p.setPen(QColor("#f3f0e8"))
            else:
                p.setPen(QColor("#5f574d"))
            p.drawText(cell, Qt.AlignmentFlag.AlignCenter, note_name(step["note"]) if step["on"] else "–")
            p.setFont(small)
            for k, (key, text, color) in enumerate((("accent", "ACC", "#d8362f"), ("slide", "SLIDE", "#3f7fd9"))):
                r = QRectF(x + 2 + k * (cw - 4) / 2, h - fh, (cw - 4) / 2 - 1, fh - 2)
                lit = step[key] and step["on"] and active
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(color) if lit else QColor("#241c16"))
                p.drawRoundedRect(r, 2, 2)
                p.setPen(QColor("#fff") if lit else QColor("#6f665b"))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


class SequencerPanel(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.steps = default_steps()

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 4)
        row.setSpacing(12)

        self.play = QPushButton("▶  Play")
        self.play.setCheckable(True)
        self.play.setFixedWidth(84)
        self.play.setToolTip("Start / stop the sequencer (Space)")
        self.play.toggled.connect(self.set_playing)

        self.bpm = QSpinBox()
        self.bpm.setRange(40, 240)
        self.bpm.setValue(120)
        self.bpm.valueChanged.connect(lambda v: engine.set_global(G_BPM, v))
        self.length = QSpinBox()
        self.length.setRange(1, STEPS)
        self.length.setValue(STEPS)
        self.length.valueChanged.connect(self.set_length)
        self.swing = self.slider(0, 60, 0, lambda v: engine.set_global(G_SWING, v / 100 * 0.6))
        self.gate = self.slider(5, 100, 50, lambda v: engine.set_global(G_GATE, v / 100))

        controls = QVBoxLayout()
        controls.setSpacing(4)
        top, bottom = QHBoxLayout(), QHBoxLayout()
        top.addWidget(self.play)
        for text, widget in (("BPM", self.bpm), ("STEPS", self.length)):
            top.addWidget(self.dim(text))
            top.addWidget(widget)
        for text, widget in (("SWING", self.swing), ("GATE", self.gate)):
            bottom.addWidget(self.dim(text))
            bottom.addWidget(widget)
        for text, slot, tip in (("Random", self.randomize, "New random pattern in the current key"),
                                ("Clear", self.clear, "Switch every step off")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bottom.addWidget(b)
        controls.addLayout(top)
        controls.addLayout(bottom)
        controls.addStretch(1)
        row.addLayout(controls)

        self.grid = StepGrid(self)
        row.addWidget(self.grid, 1)

        self.push_all()
        timer = QTimer(self)
        timer.timeout.connect(self.poll)
        timer.start(25)

    @staticmethod
    def dim(text):
        lab = QLabel(text)
        lab.setObjectName("dim")
        return lab

    @staticmethod
    def slider(low, high, value, slot):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(low, high)
        s.setValue(value)
        s.setFixedWidth(70)
        s.valueChanged.connect(slot)
        return s

    # -- engine sync -------------------------------------------------------
    def push(self, i):
        s = self.steps[i]
        self.engine.seq_step(i, s["note"], s["on"], s["accent"], s["slide"])
        self.grid.update()

    def push_all(self):
        for i in range(STEPS):
            self.push(i)
        self.engine.set_global(G_BPM, self.bpm.value())
        self.engine.set_global(G_SWING, self.swing.value() / 100 * 0.6)
        self.engine.set_global(G_GATE, self.gate.value() / 100)
        self.engine.set_global(G_SEQLEN, self.length.value())

    def set_length(self, n):
        self.engine.set_global(G_SEQLEN, n)
        self.grid.update()

    def set_playing(self, on):
        self.play.setText("■  Stop" if on else "▶  Play")
        self.engine.transport(on)

    def poll(self):
        pos = self.engine.seq_pos()
        if pos != self.grid.playhead:
            self.grid.playhead = pos
            self.grid.update()

    # -- pattern tools -----------------------------------------------------
    def clear(self):
        for s in self.steps:
            s.update(on=False, accent=False, slide=False)
        self.push_all()

    def randomize(self):
        root = next((s["note"] for s in self.steps if s["on"]), 45)
        scale = [0, 3, 5, 7, 10, 12, 15]            # minor pentatonic
        for i, s in enumerate(self.steps):
            s["note"] = root if i % 8 == 0 else root + random.choice(scale) - random.choice([0, 0, 12])
            s["on"] = i % 4 == 0 or random.random() < 0.7
            s["accent"] = random.random() < 0.25
            s["slide"] = random.random() < 0.15
        self.push_all()

    # -- patches -----------------------------------------------------------
    def to_dict(self):
        return {"bpm": self.bpm.value(), "swing": self.swing.value(), "gate": self.gate.value(),
                "length": self.length.value(), "steps": [dict(s) for s in self.steps]}

    def from_dict(self, d):
        self.bpm.setValue(int(d.get("bpm", 120)))
        self.swing.setValue(int(d.get("swing", 0)))
        self.gate.setValue(int(d.get("gate", 50)))
        self.length.setValue(int(d.get("length", STEPS)))
        steps = d.get("steps", [])
        for i in range(STEPS):
            src = steps[i] if i < len(steps) else {}
            self.steps[i] = {"note": max(12, min(108, int(src.get("note", 57)))), "on": bool(src.get("on", False)),
                             "accent": bool(src.get("accent", False)), "slide": bool(src.get("slide", False))}
        self.push_all()
