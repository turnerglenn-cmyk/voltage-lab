"""Factory patches.

A patch is plain JSON-able data (the same format the app saves to disk):

    {"name", "voices", "glide", "bend", "legato",
     "modules": [{"id", "type", "params": {name: value}, "x"?, "y"?}],
     "cables":  [[src_id, out_name, dst_id, in_name]],
     "seq"?: {"bpm", "swing", "gate", "length", "steps": [{"note", "on", "accent", "slide"}]}}

Stepped params may be given by option label ("16'") or index.
"""


def patch(name, modules, cables, voices=1, glide=0.0, bend=2, legato=True, seq=None):
    mods = [{"id": i, "type": t, "params": p} for i, t, p in modules]
    wires = []
    for c in cables:
        src, dst = c.split(">")
        s, o = src.strip().split(".", 1)
        d, i = dst.strip().split(".", 1)
        wires.append([s, o, d, i])
    out = {"name": name, "voices": voices, "glide": glide, "bend": bend, "legato": legato,
           "modules": mods, "cables": wires}
    if seq is not None:         # optional pattern for the step sequencer panel
        out["seq"] = seq
    return out


# Filter + amp section shared by most voices: filter envelope into CV1,
# amp envelope into the VCA.
def voice_tail(source, cutoff, res=0.2, env=0.4, key=0.5, fenv=None, aenv=None, drive=1.0):
    f = {"ATTACK": 0.005, "DECAY": 0.4, "SUSTAIN": 0.3, "RELEASE": 0.3}
    a = {"ATTACK": 0.004, "DECAY": 0.3, "SUSTAIN": 0.8, "RELEASE": 0.25}
    f.update(fenv or {})
    a.update(aenv or {})
    mods = [
        ("flt", "VCF", {"CUTOFF": cutoff, "RESONANCE": res, "CV1 AMT": env, "KEY TRACK": key, "DRIVE": drive}),
        ("fenv", "ENV", f),
        ("vca", "VCA", {}),
        ("aenv", "ENV", a),
    ]
    wires = [f"{source} > flt.IN", "fenv.ENV > flt.CV1", "flt.OUT > vca.IN", "aenv.ENV > vca.CV"]
    return mods, wires


def _init():
    m, w = voice_tail("osc1.SAW", 1200, res=0.25, env=0.45)
    return patch("Init — Basic Voice",
                 [("osc1", "VCO", {})] + m + [("out", "OUT", {})],
                 w + ["vca.OUT > out.LEFT"])


def _model_d_bass():
    m, w = voice_tail("mix.OUT", 110, res=0.3, env=0.5, key=0.4, drive=1.6,
                      fenv={"ATTACK": 0.002, "DECAY": 0.25, "SUSTAIN": 0.15, "RELEASE": 0.15},
                      aenv={"ATTACK": 0.002, "DECAY": 0.4, "SUSTAIN": 0.7, "RELEASE": 0.12})
    return patch("Model D Bass (3-osc ladder bass)",
                 [("osc1", "VCO", {"RANGE": "16'"}),
                  ("osc2", "VCO", {"RANGE": "16'", "FINE": 8}),
                  ("osc3", "VCO", {"RANGE": "32'"}),
                  ("mix", "MIXER", {"LEVEL 3": 0.5})] + m + [("out", "OUT", {})],
                 ["osc1.SAW > mix.IN 1", "osc2.SAW > mix.IN 2", "osc3.PULSE > mix.IN 3"] + w
                 + ["vca.OUT > out.LEFT"], glide=0.03)


def _lucky_lead():
    m, w = voice_tail("mix.OUT", 2500, res=0.15, env=0.2, key=0.6,
                      aenv={"ATTACK": 0.01, "SUSTAIN": 1.0, "RELEASE": 0.2})
    return patch("Lucky Lead (portamento solo)",
                 [("osc1", "VCO", {"FM AMT": 0.14}),
                  ("osc2", "VCO", {"FINE": -6, "FM AMT": 0.14}),
                  ("lfo", "LFO", {"RATE": 5.5, "FADE IN": 0.6}),
                  ("mix", "MIXER", {})] + m
                 + [("echo", "DELAY", {"TIME": 0.38, "MIX": 0.25}), ("out", "OUT", {})],
                 ["lfo.SINE > osc1.FM", "lfo.SINE > osc2.FM",
                  "osc1.PULSE > mix.IN 1", "osc2.SAW > mix.IN 2"] + w
                 + ["vca.OUT > echo.IN", "echo.OUT > out.LEFT"], glide=0.18)


def _brass():
    m, w = voice_tail("mix.OUT", 300, res=0.1, env=0.55,
                      fenv={"ATTACK": 0.07, "DECAY": 0.5, "SUSTAIN": 0.55},
                      aenv={"ATTACK": 0.04, "DECAY": 0.2, "SUSTAIN": 0.9})
    return patch("Switched-On Brass",
                 [("osc1", "VCO", {"FINE": -5}), ("osc2", "VCO", {"FINE": 5}),
                  ("mix", "MIXER", {})] + m
                 + [("rev", "REVERB", {"MIX": 0.2}), ("out", "OUT", {})],
                 ["osc1.SAW > mix.IN 1", "osc2.SAW > mix.IN 2"] + w
                 + ["vca.OUT > rev.IN", "rev.OUT > out.LEFT"])


def _strings():
    m, w = voice_tail("mix.OUT", 3200, res=0.05, env=0.0, key=0.3,
                      aenv={"ATTACK": 0.35, "DECAY": 0.5, "SUSTAIN": 0.85, "RELEASE": 0.9})
    return patch("String Ensemble (6-voice poly)",
                 [("osc1", "VCO", {"FINE": -7, "FM AMT": 0.08}),
                  ("osc2", "VCO", {"FINE": 7, "PWM AMT": 0.6, "FM AMT": 0.08}),
                  ("pwm", "LFO", {"RATE": 0.7}),
                  ("vib", "LFO", {"RATE": 5.2, "FADE IN": 0.8}),
                  ("mix", "MIXER", {})] + m
                 + [("rev", "REVERB", {"MIX": 0.35, "DECAY": 0.7}), ("out", "OUT", {"LEVEL": 0.3})],
                 ["pwm.TRI > osc2.PWM", "vib.SINE > osc1.FM", "vib.SINE > osc2.FM",
                  "osc1.SAW > mix.IN 1", "osc2.PULSE > mix.IN 2"] + w
                 + ["vca.OUT > rev.IN", "rev.OUT > out.LEFT"], voices=6)


def _sync_lead():
    m, w = voice_tail("osc2.SAW", 6000, res=0.1, env=0.1,
                      fenv={"ATTACK": 0.005, "DECAY": 0.9, "SUSTAIN": 0.2},
                      aenv={"SUSTAIN": 1.0})
    return patch("Sync Sweep Lead",
                 [("osc1", "VCO", {}), ("osc2", "VCO", {"TUNE": 7, "FM AMT": 0.75})] + m
                 + [("out", "OUT", {})],
                 ["osc1.PULSE > osc2.SYNC", "fenv.ENV > osc2.FM"] + w + ["vca.OUT > out.LEFT"],
                 glide=0.05)


def _zap():
    m, w = voice_tail("noise.WHITE", 220, res=1.0, env=0.6, key=1.0,
                      fenv={"ATTACK": 0.001, "DECAY": 0.12, "SUSTAIN": 0.0, "RELEASE": 0.12},
                      aenv={"ATTACK": 0.001, "DECAY": 0.35, "SUSTAIN": 0.0, "RELEASE": 0.3})
    return patch("Zap Toms (self-oscillating filter)",
                 [("noise", "NOISE", {"LEVEL": 0.05})] + m + [("out", "OUT", {})],
                 w + ["vca.OUT > out.LEFT"])


def _bubbles():
    m, w = voice_tail("osc1.PULSE", 700, res=0.55, env=0.35,
                      fenv={"ATTACK": 0.001, "DECAY": 0.1, "SUSTAIN": 0.0, "RELEASE": 0.1},
                      aenv={"SUSTAIN": 1.0, "RELEASE": 0.4})
    return patch("Sample & Hold Bubbles (hold a key)",
                 [("clock", "LFO", {"RATE": 8}), ("sh", "SH", {}),
                  ("osc1", "VCO", {"FM AMT": 0.6, "WIDTH": 0.3})] + m
                 + [("echo", "DELAY", {"TIME": 0.28, "FEEDBACK": 0.5, "MIX": 0.35}), ("out", "OUT", {})],
                 ["clock.SQR > sh.TRIG", "clock.SQR > fenv.GATE", "sh.OUT > osc1.FM"] + w
                 + ["vca.OUT > echo.IN", "echo.OUT > out.LEFT"])


def _pluck():
    m, w = voice_tail("mix.OUT", 500, res=0.3, env=0.5,
                      fenv={"ATTACK": 0.001, "DECAY": 0.18, "SUSTAIN": 0.0, "RELEASE": 0.18},
                      aenv={"ATTACK": 0.001, "DECAY": 0.5, "SUSTAIN": 0.0, "RELEASE": 0.4})
    return patch("Tape Echo Pluck (4-voice poly)",
                 [("osc1", "VCO", {"WIDTH": 0.3}), ("mix", "MIXER", {"LEVEL 2": 0.4})] + m
                 + [("echo", "DELAY", {"TIME": 0.33, "FEEDBACK": 0.45, "MIX": 0.35}),
                    ("out", "OUT", {"LEVEL": 0.45})],
                 ["osc1.PULSE > mix.IN 1", "osc1.SAW > mix.IN 2"] + w
                 + ["vca.OUT > echo.IN", "echo.OUT > out.LEFT"], voices=4)


def _pwm_pad():
    m, w = voice_tail("mix.OUT", 900, res=0.2, env=0.3,
                      fenv={"ATTACK": 0.6, "DECAY": 1.5, "SUSTAIN": 0.6, "RELEASE": 1.2},
                      aenv={"ATTACK": 0.4, "DECAY": 0.5, "SUSTAIN": 0.9, "RELEASE": 1.2})
    return patch("PWM Pad (6-voice poly)",
                 [("osc1", "VCO", {"PWM AMT": 0.7}),
                  ("osc2", "VCO", {"FINE": 9, "PWM AMT": 0.7}),
                  ("lfo1", "LFO", {"RATE": 0.35}), ("lfo2", "LFO", {"RATE": 0.5}),
                  ("mix", "MIXER", {})] + m
                 + [("rev", "REVERB", {"MIX": 0.4, "DECAY": 0.8}), ("out", "OUT", {"LEVEL": 0.3})],
                 ["lfo1.TRI > osc1.PWM", "lfo2.SINE > osc2.PWM",
                  "osc1.PULSE > mix.IN 1", "osc2.PULSE > mix.IN 2"] + w
                 + ["vca.OUT > rev.IN", "rev.OUT > out.LEFT"], voices=6)


def _seq960():
    m, w = voice_tail("osc1.SAW", 400, res=0.45, env=0.45,
                      fenv={"ATTACK": 0.001, "DECAY": 0.15, "SUSTAIN": 0.0, "RELEASE": 0.1},
                      aenv={"ATTACK": 0.001, "DECAY": 0.25, "SUSTAIN": 0.3, "RELEASE": 0.1})
    steps = dict(zip([f"STEP {i}" for i in range(1, 9)], [0, 12, 7, 0, 10, 12, 3, 7]))
    return patch("960 Sequence (press Play; keys transpose)",
                 [("seq", "SEQ", steps), ("osc1", "VCO", {"RANGE": "16'"})] + m
                 + [("echo", "DELAY", {"TIME": 0.375, "FEEDBACK": 0.35, "MIX": 0.2}), ("out", "OUT", {})],
                 ["seq.CV > osc1.V/OCT", "seq.GATE > fenv.GATE", "seq.GATE > aenv.GATE"] + w
                 + ["vca.OUT > echo.IN", "echo.OUT > out.LEFT"],
                 seq={"bpm": 120, "steps": []})     # empty note pattern: the 960 module does the playing


FACTORY = [f() for f in (_init, _model_d_bass, _lucky_lead, _brass, _strings, _sync_lead,
                         _zap, _bubbles, _pluck, _pwm_pad, _seq960)]
