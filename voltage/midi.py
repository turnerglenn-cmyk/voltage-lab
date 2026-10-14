"""MIDI keyboard input.

Linux: read straight from ALSA raw MIDI devices. Windows: the built-in
winmm MIDI API. macOS: CoreMIDI. Either way nothing extra needs installing.

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


class CoreMidiIn(threading.Thread):
    """macOS: one CoreMIDI input port connected to every source (keyboards,
    interfaces, the IAC bus). New sources are connected as they appear."""

    UTF8 = 0x08000100       # kCFStringEncodingUTF8

    def __init__(self, engine):
        super().__init__(daemon=True)
        import ctypes
        import ctypes.util
        import platform
        self.ct = ctypes
        self.engine = engine
        self.sustain = False
        self.sustained = set()
        self.connected = set()      # MIDIEndpointRefs we are listening to
        self.parsers = {}           # source -> running-status parser state
        self.lock = threading.Lock()
        # Packets in a MIDIPacketList are 4-byte aligned on ARM, packed on Intel.
        self.align = platform.machine() == "arm64"
        self.ok = False
        try:
            cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
            cm = self.cm = ctypes.CDLL(ctypes.util.find_library("CoreMIDI"))
        except OSError:
            return
        u32, vp = ctypes.c_uint32, ctypes.c_void_p
        cf.CFStringCreateWithCString.restype = vp
        cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, u32]
        read_proc = ctypes.CFUNCTYPE(None, vp, vp, vp)
        cm.MIDIClientCreate.restype = ctypes.c_int32
        cm.MIDIClientCreate.argtypes = [vp, vp, vp, ctypes.POINTER(u32)]
        cm.MIDIInputPortCreate.restype = ctypes.c_int32
        cm.MIDIInputPortCreate.argtypes = [u32, vp, read_proc, vp, ctypes.POINTER(u32)]
        cm.MIDIGetNumberOfSources.restype = ctypes.c_size_t
        cm.MIDIGetNumberOfSources.argtypes = []
        cm.MIDIGetSource.restype = u32
        cm.MIDIGetSource.argtypes = [ctypes.c_size_t]
        cm.MIDIPortConnectSource.restype = ctypes.c_int32
        cm.MIDIPortConnectSource.argtypes = [u32, u32, vp]

        def cfstr(text):
            return cf.CFStringCreateWithCString(None, text.encode(), self.UTF8)

        self._cb = read_proc(self._on_packets)      # keep a reference so it isn't garbage collected
        client, port = u32(), u32()
        if cm.MIDIClientCreate(cfstr("Voltage Lab"), None, None, ctypes.byref(client)) != 0:
            return
        if cm.MIDIInputPortCreate(client, cfstr("Voltage Lab In"), self._cb, None, ctypes.byref(port)) != 0:
            return
        self.port = port.value
        self.ok = True

    @property
    def device_count(self):
        return len(self.connected)

    def _on_packets(self, pktlist, _ref, src):
        """Runs on CoreMIDI's own thread."""
        ct = self.ct
        try:
            n = ct.c_uint32.from_address(pktlist).value
            pkt = pktlist + 4
            with self.lock:
                st = self.parsers.setdefault(src or 0, {"status": 0, "data": []})
                for _ in range(n):
                    length = ct.c_uint16.from_address(pkt + 8).value
                    for byte in ct.string_at(pkt + 10, length):
                        self.feed(st, byte)
                    pkt += 10 + length
                    if self.align:
                        pkt = (pkt + 3) & ~3
        except Exception:           # never let an error escape into CoreMIDI's thread
            pass

    def scan(self):
        cm = self.cm
        current = {cm.MIDIGetSource(i) for i in range(cm.MIDIGetNumberOfSources())}
        current.discard(0)
        for src in current - self.connected:
            if cm.MIDIPortConnectSource(self.port, src, self.ct.c_void_p(src)) == 0:
                self.connected.add(src)
        self.connected &= current

    def run(self):
        if not self.ok:
            return
        while True:
            try:
                self.scan()
            except Exception:
                pass
            time.sleep(2)

    feed = AlsaMidiIn.feed
    message = AlsaMidiIn.message


MidiIn = {"win32": WinMidiIn, "darwin": CoreMidiIn}.get(sys.platform, AlsaMidiIn)
