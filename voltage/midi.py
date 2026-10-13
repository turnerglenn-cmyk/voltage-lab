"""MIDI keyboard input.

Linux: read straight from ALSA raw MIDI devices. Windows: the built-in
winmm MIDI API. Either way nothing extra needs installing.

Devices are picked up automatically when plugged in. Handles notes,
pitch bend, mod wheel (CC1), sustain pedal (CC64) and all-notes-off.
"""
import glob
import os
import select
import sys
import threading
import time


class AlsaMidiIn(threading.Thread):
    def __init__(self, engine):
        super().__init__(daemon=True)
        self.engine = engine
        self.fds = {}          # fd -> parser state
        self.paths = {}        # path -> fd
        self.sustain = False
        self.sustained = set()

    @property
    def device_count(self):
        return len(self.paths)

    def scan(self):
        for path in glob.glob("/dev/snd/midiC*D*"):
            if path in self.paths:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            self.paths[path] = fd
            self.fds[fd] = {"status": 0, "data": []}

    def drop(self, fd):
        self.fds.pop(fd, None)
        for path, f in list(self.paths.items()):
            if f == fd:
                del self.paths[path]
        try:
            os.close(fd)
        except OSError:
            pass

    def run(self):
        last_scan = 0.0
        while True:
            if time.time() - last_scan > 2:
                self.scan()
                last_scan = time.time()
            if not self.fds:
                time.sleep(0.5)
                continue
            ready, _, _ = select.select(list(self.fds), [], [], 0.5)
            for fd in ready:
                try:
                    data = os.read(fd, 256)
                except BlockingIOError:
                    continue
                except OSError:
                    self.drop(fd)
                    continue
                if not data:
                    self.drop(fd)
                    continue
                for byte in data:
                    self.feed(self.fds[fd], byte)

    def feed(self, st, byte):
        if byte >= 0xF8:            # realtime, may appear anywhere
            return
        if byte & 0x80:
            st["status"] = byte if byte < 0xF0 else 0
            st["data"] = []
            return
        if not st["status"]:
            return
        st["data"].append(byte)
        kind = st["status"] & 0xF0
        if len(st["data"]) < (1 if kind in (0xC0, 0xD0) else 2):
            return
        a, b = st["data"][0], (st["data"] + [0])[1]
        st["data"] = []             # keep status: running status
        self.message(kind, a, b)

    def message(self, kind, a, b):
        e = self.engine
        if kind == 0x90 and b > 0:
            self.sustained.discard(a)
            e.note_on(a, b / 127.0)
        elif kind == 0x80 or kind == 0x90:
            if self.sustain:
                self.sustained.add(a)
            else:
                e.note_off(a)
        elif kind == 0xE0:
            e.bend(((b << 7 | a) - 8192) / 8192.0)
        elif kind == 0xB0:
            if a == 1:
                e.mod(b / 127.0)
            elif a == 64:
                self.sustain = b >= 64
                if not self.sustain:
                    for n in self.sustained:
                        e.note_off(n)
                    self.sustained.clear()
            elif a in (120, 123):
                e.panic()


class WinMidiIn(threading.Thread):
    """Windows: opens every MIDI input with winmm and reopens them when the
    number of devices changes (keyboard plugged in or out)."""

    MIM_DATA = 0x3C3
    CALLBACK_FUNCTION = 0x30000

    def __init__(self, engine):
        super().__init__(daemon=True)
        import ctypes
        import ctypes.wintypes
        from ctypes import wintypes
        self.ct = ctypes
        self.engine = engine
        self.sustain = False
        self.sustained = set()
        self.handles = []
        self.count = -1
        w = self.winmm = ctypes.WinDLL("winmm")
        w.midiInGetNumDevs.restype = wintypes.UINT
        w.midiInOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.c_void_p,
                                 ctypes.c_void_p, wintypes.DWORD]
        w.midiInOpen.restype = wintypes.UINT
        for name in ("midiInStart", "midiInStop", "midiInReset", "midiInClose"):
            fn = getattr(w, name)
            fn.argtypes = [wintypes.HANDLE]
            fn.restype = wintypes.UINT
        proto = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t,
                                   ctypes.c_size_t, ctypes.c_size_t)
        self._cb = proto(self._on_midi)     # keep a reference so it isn't garbage collected

    @property
    def device_count(self):
        return len(self.handles)

    def _on_midi(self, handle, msg, instance, param1, param2):
        if msg != self.MIM_DATA:
            return
        status, a, b = param1 & 0xFF, (param1 >> 8) & 0x7F, (param1 >> 16) & 0x7F
        if 0x80 <= status < 0xF0:
            try:
                self.message(status & 0xF0, a, b)
            except Exception:       # never let an error escape into the driver's thread
                pass

    def _close_all(self):
        for h in self.handles:
            self.winmm.midiInStop(h)
            self.winmm.midiInReset(h)
            self.winmm.midiInClose(h)
        self.handles = []

    def _open_all(self, n):
        wintypes = self.ct.wintypes
        for dev in range(n):
            h = wintypes.HANDLE()
            if self.winmm.midiInOpen(self.ct.byref(h), dev, self.ct.cast(self._cb, self.ct.c_void_p),
                                     None, self.CALLBACK_FUNCTION) == 0:
                self.winmm.midiInStart(h)
                self.handles.append(h)

    def run(self):
        while True:
            n = self.winmm.midiInGetNumDevs()
            if n != self.count:
                self._close_all()
                self._open_all(n)
                self.count = n
            time.sleep(2)

    message = AlsaMidiIn.message


MidiIn = WinMidiIn if sys.platform == "win32" else AlsaMidiIn
