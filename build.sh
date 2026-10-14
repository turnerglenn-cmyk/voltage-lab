#!/bin/sh
# Builds the audio engine. Needs a C++ compiler and the PortAudio library.
set -e
cd "$(dirname "$0")"
if [ "$(uname)" = Darwin ]; then
    # macOS: Xcode command line tools (xcode-select --install) and Homebrew's portaudio.
    PREFIX=$(brew --prefix portaudio 2>/dev/null || true)
    if [ -z "$PREFIX" ] || [ ! -d "$PREFIX/lib" ]; then
        echo "PortAudio not found. Install it with: brew install portaudio" >&2
        exit 1
    fi
    c++ -O3 -fno-math-errno -fno-trapping-math -std=c++17 -dynamiclib -o engine/libsynth.dylib \
        engine/synth.cpp -L"$PREFIX/lib" -lportaudio
    echo "built engine/libsynth.dylib"
    exit 0
fi
PA=$(ls /usr/lib/*/libportaudio.so.2 /usr/lib/libportaudio.so.2 /usr/lib64/libportaudio.so.2 2>/dev/null | head -1)
if [ -z "$PA" ]; then
    echo "PortAudio not found. Install it (e.g. sudo apt install libportaudio2)." >&2
    exit 1
fi
g++ -O3 -march=native -fno-math-errno -fno-trapping-math -std=c++17 -shared -fPIC -o engine/libsynth.so engine/synth.cpp "$PA" -lpthread
echo "built engine/libsynth.so"
