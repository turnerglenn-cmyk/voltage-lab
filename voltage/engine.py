"""ctypes wrapper around the C++ audio engine (engine/libsynth.so; engine/synth.dll on Windows,
engine/libsynth.dylib on macOS)."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading

ENGINE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
LIB = os.path.join(ENGINE_DIR, {"win32": "synth.dll", "darwin": "libsynth.dylib"}.get(sys.platform, "libsynth.so"))

LIN, EXP, STEP = 0, 1, 2
G_MASTER, G_GLIDE, G_BEND, G_LEGATO, G_BPM, G_SWING, G_GATE, G_SEQLEN = range(8)


class Engine:
    def __init__(self, sr=48000):
        if sys.platform == "win32":
            os.add_dll_directory(ENGINE_DIR)    # PortAudio and the C++ runtime sit next to synth.dll
        L = self.lib = ctypes.CDLL(LIB)
        c, f, i, p = ctypes.c_char_p, ctypes.c_float, ctypes.c_int, ctypes.c_void_p
        fp = ctypes.POINTER(ctypes.c_float)
        for name, res, args in [
            ("sy_create", p, [f]), ("sy_describe", c, []),
            ("sy_add_module", i, [p, c]), ("sy_remove_module", None, [p, i]),
            ("sy_clear", None, [p]), ("sy_set_param", None, [p, i, i, f]),
            ("sy_connect", None, [p, i, i, i, i, i]), ("sy_set_voices", None, [p, i]),
            ("sy_set_global", None, [p, i, f]), ("sy_note_on", None, [p, i, f]),
            ("sy_note_off", None, [p, i]), ("sy_bend", None, [p, f]), ("sy_mod", None, [p, f]),
            ("sy_all_off", None, [p]), ("sy_render", None, [p, fp, i]),
            ("sy_get_scope", None, [p, fp, i]), ("sy_get_peak", f, [p]), ("sy_get_cpu", f, [p]),
            ("sy_seq_step", None, [p, i, i, i, i, i]), ("sy_transport", None, [p, i]),
            ("sy_seq_pos", i, [p]),
            ("sy_start_audio", i, [p, i]), ("sy_audio_error", c, []), ("sy_stop_audio", None, []),
        ]:
            fn = getattr(L, name)
            fn.restype, fn.argtypes = res, args
        self.sr = sr
        self.h = L.sy_create(sr)
        self.types = {d["type"]: d for d in json.loads(L.sy_describe().decode())}
        for d in self.types.values():
            for prm in d["params"]:
                prm["opts"] = prm["opts"].split("|") if prm["opts"] else []
        self.active = set()     # notes currently held, for the on-screen keyboard
        self.backend = "off"
        self._pull = None

    # -- audio output ------------------------------------------------------
    def start_audio(self, frames=256):
        """Open the sound card. Falls back to piping audio through PulseAudio/PipeWire."""
        if self.lib.sy_start_audio(self.h, frames) == 0:
            self.backend = "PortAudio"
            return
        err = self.lib.sy_audio_error().decode()
        player = shutil.which("pacat")
        if not player:
            self.backend = f"no audio ({err})"
            return
        proc = subprocess.Popen(
            [player, "--raw", "--format=float32le", f"--rate={self.sr}", "--channels=2", "--latency-msec=30"],
            stdin=subprocess.PIPE)
        self._pull = proc

        def pump():
            buf = (ctypes.c_float * (frames * 2))()
            try:
                while self._pull is proc:
                    self.lib.sy_render(self.h, buf, frames)
                    proc.stdin.write(bytes(buf))
            except (BrokenPipeError, ValueError):
                pass

        threading.Thread(target=pump, daemon=True).start()
        self.backend = "PulseAudio pipe"

    def stop_audio(self):
        self.lib.sy_stop_audio()
        proc, self._pull = self._pull, None
        if proc:
            proc.kill()

    def render(self, frames):
        """Render offline; returns a ctypes float array of interleaved stereo."""
        buf = (ctypes.c_float * (frames * 2))()
        self.lib.sy_render(self.h, buf, frames)
        return buf

    # -- rack structure ----------------------------------------------------
    def add_module(self, type_):
        return self.lib.sy_add_module(self.h, type_.encode())

    def remove_module(self, mid):
        self.lib.sy_remove_module(self.h, mid)

    def clear(self):
        self.lib.sy_clear(self.h)

    def set_param(self, mid, index, value):
        self.lib.sy_set_param(self.h, mid, index, float(value))

    def connect(self, src, out, dst, inp, on=True):
        self.lib.sy_connect(self.h, src, out, dst, inp, int(on))

    def set_voices(self, n):
        self.active.clear()
        self.lib.sy_set_voices(self.h, n)

    def set_global(self, which, value):
        self.lib.sy_set_global(self.h, which, float(value))

    # -- performance -------------------------------------------------------
    def note_on(self, note, vel=0.8):
        if 0 <= note <= 127:
            self.active.add(note)
            self.lib.sy_note_on(self.h, note, vel)

    def note_off(self, note):
        self.active.discard(note)
        self.lib.sy_note_off(self.h, note)

    def bend(self, v):
        self.lib.sy_bend(self.h, v)

    def mod(self, v):
        self.lib.sy_mod(self.h, v)

    def panic(self):
        self.active.clear()
        self.lib.sy_all_off(self.h)

    # -- pattern sequencer ------------------------------------------------
    def seq_step(self, index, note, on, accent, slide):
        self.lib.sy_seq_step(self.h, index, int(note), int(on), int(accent), int(slide))

    def transport(self, play):
        self.lib.sy_transport(self.h, int(play))

    def seq_pos(self):
        return self.lib.sy_seq_pos(self.h)

    # -- metering ----------------------------------------------------------
    def scope(self, n=1024):
        buf = (ctypes.c_float * n)()
        self.lib.sy_get_scope(self.h, buf, n)
        return buf

    def peak(self):
        return self.lib.sy_get_peak(self.h)

    def cpu(self):
        return self.lib.sy_get_cpu(self.h)


# -- knob position <-> parameter value ------------------------------------

def pos_to_value(prm, pos):
    pos = min(1.0, max(0.0, pos))
    if prm["curve"] == STEP:
        return float(round(prm["min"] + pos * (prm["max"] - prm["min"])))
    if prm["curve"] == EXP:
        return prm["min"] * (prm["max"] / prm["min"]) ** pos
    return prm["min"] + pos * (prm["max"] - prm["min"])


def value_to_pos(prm, value):
    import math
    value = min(prm["max"], max(prm["min"], value))
    if prm["curve"] == EXP:
        return math.log(value / prm["min"]) / math.log(prm["max"] / prm["min"])
    return (value - prm["min"]) / (prm["max"] - prm["min"])


def format_value(prm, value):
    if prm["curve"] == STEP:
        if not prm["opts"]:                     # plain integer steps
            return f"{int(round(value)):+d}{prm['unit']}" if prm["unit"] else str(int(round(value)))
        return prm["opts"][int(round(value))]
    unit = prm["unit"]
    if unit == "Hz":
        return f"{value / 1000:.1f}k" if value >= 1000 else f"{value:.3g}Hz"
    if unit == "s":
        return f"{value * 1000:.0f}ms" if value < 1 else f"{value:.2f}s"
    if unit == "%":
        return f"{value * 100:.0f}%"
    if unit in ("st", "ct"):
        return f"{value:+.1f}{unit}" if unit == "st" else f"{value:+.0f}ct"
    if unit == "x":
        return f"x{value:.2f}"
    return f"{value:.2f}"
