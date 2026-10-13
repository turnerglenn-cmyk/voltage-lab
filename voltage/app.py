"""Main window: toolbar, rack, and the keyboard / performance strip."""
import json
import os
import sys

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QFileDialog,
                             QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                             QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from .engine import G_BEND, G_GLIDE, G_LEGATO, G_MASTER, Engine
from .midi import MidiIn
from .patches import FACTORY
from .rack import RackScene, RackView
from .sequencer import SequencerPanel
from .widgets import Piano, Scope


def _user_dir():
    """Where saved patches live. Next to the code when run from source on Linux;
    in the user's own folder when installed (Program Files is read-only)."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Voltage Lab", "patches")
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".local", "share", "voltage-lab", "patches")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "patches")


USER_DIR = _user_dir()

# Computer keyboard: the home row is the white keys, the row above the black keys.
KEYMAP = {Qt.Key.Key_A: 0, Qt.Key.Key_W: 1, Qt.Key.Key_S: 2, Qt.Key.Key_E: 3, Qt.Key.Key_D: 4,
          Qt.Key.Key_F: 5, Qt.Key.Key_T: 6, Qt.Key.Key_G: 7, Qt.Key.Key_Y: 8, Qt.Key.Key_H: 9,
          Qt.Key.Key_U: 10, Qt.Key.Key_J: 11, Qt.Key.Key_K: 12, Qt.Key.Key_O: 13, Qt.Key.Key_L: 14,
          Qt.Key.Key_P: 15, Qt.Key.Key_Semicolon: 16}

STYLE = """
QMainWindow, QWidget { background: #1b1410; color: #e9e6df; font-size: 12px; }
QPushButton, QComboBox, QSpinBox { background: #2b211a; border: 1px solid #5a4636; border-radius: 3px;
    padding: 4px 9px; min-height: 18px; }
QPushButton:hover, QComboBox:hover { border-color: #e8b923; }
QPushButton:pressed { background: #e8b923; color: #111; }
QComboBox QAbstractItemView, QMenu { background: #2b211a; selection-background-color: #e8b923;
    selection-color: #111; border: 1px solid #5a4636; }
QMenu::item { padding: 5px 22px; }
QMenu::item:selected { background: #e8b923; color: #111; }
QLabel#dim { color: #9c9488; }
QLabel#brand { font-size: 15px; font-weight: bold; letter-spacing: 3px; color: #f3f0e8; }
QSlider::groove:horizontal { height: 4px; background: #000; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -6px 0; background: #cfcfcf; border-radius: 3px; }
"""


class MainWindow(QMainWindow):
    def __init__(self, audio=True):
        super().__init__()
        self.setWindowTitle("Voltage Lab")
        self.resize(1500, 900)
        self.engine = Engine()
        self.scene = RackScene(self.engine)
        self.view = RackView(self.scene)
        self.octave = 0
        self.keys_down = {}

        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addLayout(self.build_toolbar())
        col.addWidget(self.view, 1)
        self.seq = SequencerPanel(self.engine)
        col.addWidget(self.seq)
        col.addLayout(self.build_strip())
        self.setCentralWidget(central)

        self.load(FACTORY[0])
        if audio:
            self.engine.start_audio()
        self.midi = MidiIn(self.engine)
        self.midi.start()
        QApplication.instance().installEventFilter(self)
        timer = QTimer(self)
        timer.timeout.connect(self.refresh_status)
        timer.start(500)

    # -- layout ------------------------------------------------------------
    def build_toolbar(self):
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(8)
        brand = QLabel("VOLTAGE LAB")
        brand.setObjectName("brand")
        bar.addWidget(brand)
        bar.addSpacing(14)

        self.presets = QComboBox()
        self.presets.setMinimumWidth(280)
        for p in FACTORY:
            self.presets.addItem(p["name"])
        self.presets.activated.connect(lambda i: self.load(FACTORY[i]))
        bar.addWidget(self.presets)

        add = QPushButton("Add module ▾")
        menu = QMenu(add)
        for t, d in self.engine.types.items():
            menu.addAction(f"{d['title'].title()}  ({d['model']})", lambda t=t: self.add_module(t))
        add.setMenu(menu)
        bar.addWidget(add)
        for text, slot in (("New", self.new_patch), ("Open…", self.open_patch), ("Save…", self.save_patch)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.status = QLabel()
        self.status.setObjectName("dim")
        bar.addWidget(self.status)
        panic = QPushButton("Panic")
        panic.setToolTip("Silence all notes")
        panic.clicked.connect(self.engine.panic)
        bar.addWidget(panic)
        return bar

    def build_strip(self):
        strip = QHBoxLayout()
        strip.setContentsMargins(12, 8, 12, 10)
        strip.setSpacing(14)

        def labelled(text, widget):
            box = QVBoxLayout()
            box.setSpacing(3)
            lab = QLabel(text)
            lab.setObjectName("dim")
            box.addWidget(lab)
            box.addWidget(widget)
            box.addStretch(1)
            strip.addLayout(box)

        self.voices = QSpinBox()
        self.voices.setRange(1, 8)
        self.voices.setToolTip("1 = monophonic, like the original modulars. More = polyphonic.")
        self.voices.valueChanged.connect(self.engine.set_voices)
        labelled("VOICES", self.voices)

        self.glide = QSlider(Qt.Orientation.Horizontal)
        self.glide.setRange(0, 100)
        self.glide.setFixedWidth(90)
        self.glide.valueChanged.connect(lambda v: self.engine.set_global(G_GLIDE, (v / 100) ** 2 * 2))
        labelled("GLIDE", self.glide)

        self.bend = QSpinBox()
        self.bend.setRange(0, 12)
        self.bend.valueChanged.connect(lambda v: self.engine.set_global(G_BEND, v))
        labelled("BEND ±st", self.bend)

        self.legato = QCheckBox("Legato")
        self.legato.setToolTip("Mono mode: overlapping notes do not retrigger the envelopes")
        self.legato.toggled.connect(lambda on: self.engine.set_global(G_LEGATO, 1 if on else 0))
        self.oct_label = QLabel()
        self.oct_label.setObjectName("dim")
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addWidget(self.oct_label)
        box.addWidget(self.legato)
        box.addStretch(1)
        strip.addLayout(box)

        self.piano = Piano(self.engine)
        strip.addWidget(self.piano, 1)
        strip.addWidget(Scope(self.engine))

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(lambda v: self.engine.set_global(G_MASTER, (v / 100) ** 2 * 1.4))
        self.volume.setValue(70)
        labelled("VOLUME", self.volume)
        self.set_octave(0)
        return strip

    def refresh_status(self):
        n = self.midi.device_count
        midi = f"{n} MIDI device{'s' if n != 1 else ''}" if n else "no MIDI keyboard"
        self.status.setText(f"{self.engine.backend} · {self.engine.sr // 1000} kHz · "
                            f"CPU {self.engine.cpu() * 100:.0f}% · {midi}")

    # -- patches -----------------------------------------------------------
    def load(self, patch):
        self.engine.panic()
        self.scene.load_patch(patch)
        self.voices.setValue(patch.get("voices", 1))
        self.glide.setValue(round((patch.get("glide", 0) / 2) ** 0.5 * 100))
        self.bend.setValue(patch.get("bend", 2))
        self.legato.setChecked(patch.get("legato", True))
        # setValue/setChecked skip the signal when the value is unchanged; push everything anyway.
        self.engine.set_voices(self.voices.value())
        self.engine.set_global(G_GLIDE, patch.get("glide", 0))
        self.engine.set_global(G_BEND, self.bend.value())
        self.engine.set_global(G_LEGATO, 1 if self.legato.isChecked() else 0)
        if "seq" in patch:          # patches without a pattern keep the one that is playing
            self.seq.from_dict(patch["seq"])
        self.presets.setCurrentIndex(self.presets.findText(patch.get("name", "")))
        QTimer.singleShot(0, self.view.fit_rack)

    def add_module(self, type_):
        item = self.scene.add_module(type_)
        self.view.ensureVisible(item)

    def new_patch(self):
        self.load({"name": "Untitled", "modules": [{"id": "out", "type": "OUT", "params": {}}], "cables": []})

    def open_patch(self):
        os.makedirs(USER_DIR, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Open patch", USER_DIR, "Patches (*.json)")
        if not path:
            return
        try:
            with open(path) as f:
                self.load(json.load(f))
        except (OSError, ValueError, KeyError, TypeError) as err:
            QMessageBox.warning(self, "Open patch", f"Could not load this patch:\n{err}")

    def save_patch(self):
        os.makedirs(USER_DIR, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save patch", USER_DIR, "Patches (*.json)")
        if not path:
            return
        if not path.endswith(".json"):
            path += ".json"
        patch = self.scene.to_patch()
        patch.update(name=os.path.splitext(os.path.basename(path))[0], voices=self.voices.value(),
                     glide=(self.glide.value() / 100) ** 2 * 2, bend=self.bend.value(),
                     legato=self.legato.isChecked(), seq=self.seq.to_dict())
        try:
            with open(path, "w") as f:
                json.dump(patch, f, indent=1)
        except OSError as err:
            QMessageBox.warning(self, "Save patch", f"Could not save:\n{err}")

    # -- computer keyboard ---------------------------------------------------
    def set_octave(self, octave):
        self.octave = max(-3, min(3, octave))
        self.oct_label.setText(f"KEYS A–; · OCT {self.octave:+d} (Z/X)")

    def eventFilter(self, obj, ev):
        kind = ev.type()
        if kind not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return False
        if not self.isActiveWindow() or ev.modifiers() & ~Qt.KeyboardModifier.ShiftModifier:
            return False
        if isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox)):
            return False
        key = ev.key()
        if key in KEYMAP:
            if ev.isAutoRepeat():
                return True
            if kind == QEvent.Type.KeyPress and key not in self.keys_down:
                note = 60 + 12 * self.octave + KEYMAP[key]
                self.keys_down[key] = note
                self.engine.note_on(note, 0.8)
            elif kind == QEvent.Type.KeyRelease and key in self.keys_down:
                self.engine.note_off(self.keys_down.pop(key))
            return True
        if key == Qt.Key.Key_Space:
            if kind == QEvent.Type.KeyPress and not ev.isAutoRepeat():
                self.seq.play.toggle()
            return True
        if kind == QEvent.Type.KeyPress and key in (Qt.Key.Key_Z, Qt.Key.Key_X):
            self.set_octave(self.octave + (1 if key == Qt.Key.Key_X else -1))
            return True
        return False

    def closeEvent(self, ev):
        self.engine.transport(False)
        self.engine.stop_audio()
        super().closeEvent(ev)


def main(argv):
    app = QApplication(argv)
    app.setStyleSheet(STYLE)
    win = MainWindow(audio="--no-audio" not in argv)
    win.show()
    return app.exec()
