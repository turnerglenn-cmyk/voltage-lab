# Voltage Lab

A desktop modular synthesizer in the style of the Moog modular systems: black
panels, skirted knobs, patch cables, and a transistor-ladder filter. Patch it
freely, or start from the factory patches (Minimoog-style bass, portamento
lead, brass, string ensemble, sync lead, sample & hold, tape echo and more).

## Install

**Windows:** download `VoltageLab-<version>-Setup.exe` from the
[Releases page](https://github.com/turnerglenn-cmyk/voltage-lab/releases) and run it.
Windows may show "Windows protected your PC" because the installer isn't code-signed:
click **More info → Run anyway**. Saved patches go to `%APPDATA%\Voltage Lab\patches`.

**macOS** (11 Big Sur or later): from the same Releases page download
`VoltageLab-<version>-macOS-AppleSilicon.dmg` (M1 and later) or `…-macOS-Intel.dmg` (older Macs;
Apple menu → About This Mac says which). Open it and drag **Voltage Lab** into Applications.
The app isn't notarised by Apple, so the first launch is blocked: open it once, click **Done**, then go to
**System Settings → Privacy & Security**, scroll down and click **Open Anyway**. After that it opens normally.
Saved patches go to `~/Library/Application Support/Voltage Lab/patches`.

**Linux (from source):**

```bash
./run.sh
```

The first run compiles the audio engine (needs `g++` and `libportaudio2`).
The UI needs Python 3 with PyQt6 (`sudo apt install python3-pyqt6` or `pip install pyqt6`).

**macOS (from source):** `xcode-select --install`, `brew install portaudio`, `pip3 install pyqt6`, then `./run.sh`.

## Playing

- **Computer keys**: `A S D F G H J K L ;` are white keys, `W E T Y U O P` black keys, `Z` / `X` shift octave.
- **Mouse**: click or drag across the on-screen keyboard.
- **MIDI keyboard**: plug it in; it is picked up automatically (notes, velocity, pitch bend, mod wheel, sustain).
- **Voices**: 1 is monophonic like the originals; up to 8 makes any patch polyphonic.

## Sequencer

The strip above the keyboard is a 16-step pattern sequencer that plays whatever patch is loaded.

- **Space** or the Play button starts and stops it. BPM, number of steps, swing and gate length sit beside it.
- Click a step to switch it on or off; drag it up/down (or use the wheel, Shift for octaves) to change its note.
- **ACC** plays the step at full velocity (turn up an envelope's VELOCITY knob to hear it).
  **SLIDE** ties the step into the next one with a short glide, TB-303 style.
- The pattern is saved with your patch. Switching factory patches keeps the pattern running, so you can audition sounds against it.

There is also a **Sequencer (960)** module for the rack: eight tuned steps with CV and GATE outputs.
It follows the same transport, or its own CLOCK input (for example an LFO square wave).
The "960 Sequence" factory patch shows it in use.

## Patching

- Drag from an output jack (white label) to an input jack to run a cable. Several cables into one input are mixed.
- Drag a cable off an input to move or remove it; right-click a jack to unplug everything on it.
- Knobs: drag up/down, Shift for fine, wheel to nudge, double-click to reset.
- Drag a panel to move a module; right-click it (or press Delete) to remove it. Ctrl+wheel (Cmd+wheel on a Mac) zooms the rack.
- Oscillators and the filter follow the keyboard internally, and envelopes follow the keyboard gate
  unless you patch their GATE input, so a basic voice needs only a few cables.

## Modules

| Module | Based on | Notes |
|---|---|---|
| Oscillator | 921 | sine / triangle / saw / pulse, PWM, FM, hard sync, slight drift |
| Ladder Filter | 904A | 24 dB ladder with saturating feedback, self-oscillates; LP24 / LP12 / BP / HP |
| Envelope | 911 | ADSR, velocity amount, inverted output |
| Amplifier | 902 | linear / exponential response |
| Mixer | CP3 | four inputs, overdrives when pushed |
| Noise | 903A | white and pink |
| Low Freq Osc, Sample & Hold | | fade-in for delayed vibrato; S&H has its own clock and noise |
| Tape Echo, Reverb | 905 | shared by all voices |
| Sequencer | 960 | eight steps in semitones, follows the transport or an external clock |
| Keyboard CV | 950 | pitch, gate, velocity, mod wheel as patchable signals |

## Layout

- `engine/synth.cpp` — real-time C++ engine: module graph, voices, DSP. Exposes a small C API.
- `voltage/` — PyQt6 app: `rack.py` (panels, knobs, cables), `app.py` (window), `patches.py` (factory patches), `sequencer.py` (pattern panel), `midi.py`, `engine.py` (bindings).
- `patches/` — your saved patches (JSON) when run from source.
- `packaging/`, `.github/workflows/` — Windows installer, macOS app icon, and the GitHub builds that make them.

A new module is one struct plus one descriptor row in `synth.cpp`; the UI builds its panel from the descriptor.

## Releasing a new version

Push a version tag and GitHub builds the Windows installer and the two macOS disk images
and attaches them all to one release:

```bash
git tag v0.1.1
git push --tags
```

Progress shows under the repository's **Actions** tab (about 5–10 minutes). The builds are defined in
`.github/workflows/windows.yml` and `.github/workflows/macos.yml`; the Windows installer itself in
`packaging/windows/installer.iss`.

## Licence

GPL-3.0-or-later (see `LICENSE`). The Windows and macOS builds bundle PyQt6 (GPL) and PortAudio (MIT).
