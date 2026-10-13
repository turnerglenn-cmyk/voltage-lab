#!/bin/sh
# Starts Voltage Lab, building the audio engine first if needed.
cd "$(dirname "$0")"
if [ ! -f engine/libsynth.so ] || [ engine/synth.cpp -nt engine/libsynth.so ]; then
    ./build.sh || exit 1
fi
exec python3 main.py "$@"
