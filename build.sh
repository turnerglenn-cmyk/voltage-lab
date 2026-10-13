#!/bin/sh
# Builds the audio engine. Needs g++ and the PortAudio runtime library.
set -e
cd "$(dirname "$0")"
PA=$(ls /usr/lib/*/libportaudio.so.2 /usr/lib/libportaudio.so.2 /usr/lib64/libportaudio.so.2 2>/dev/null | head -1)
if [ -z "$PA" ]; then
    echo "PortAudio not found. Install it (e.g. sudo apt install libportaudio2)." >&2
    exit 1
fi
g++ -O3 -march=native -fno-math-errno -fno-trapping-math -std=c++17 -shared -fPIC -o engine/libsynth.so engine/synth.cpp "$PA" -lpthread
echo "built engine/libsynth.so"
