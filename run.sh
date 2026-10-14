#!/bin/sh
# Starts Voltage Lab, building the audio engine first if needed.
cd "$(dirname "$0")"
LIB=engine/libsynth.so
[ "$(uname)" = Darwin ] && LIB=engine/libsynth.dylib
if [ ! -f "$LIB" ] || [ engine/synth.cpp -nt "$LIB" ]; then
    ./build.sh || exit 1
fi
exec python3 main.py "$@"
