// Voltage Lab — real-time modular synth engine.
//
// A rack is a graph of modules joined by cables. Every module exists once per
// voice, so the same patch plays mono (classic modular) or polyphonic.
// Signals are floats: audio is nominally +-1, pitch is 1.0 per octave with
// 0 = middle C, gates are 0/1, envelopes 0..1, LFOs +-1.
//
// Threading: the UI thread owns the structure (modules, cables) and edits it
// under `mx`; the audio thread try-locks and plays silence for a block if the
// rack is being edited. Parameters are atomics, notes go through a queue.

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__)
#include <xmmintrin.h>
#endif

// Denormal numbers make filters and reverb tails crawl, so flush them to zero
// on the audio thread: MXCSR FTZ+DAZ on x86, FPCR.FZ on ARM (Apple Silicon).
static inline void flushDenormals() {
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__)
    _mm_setcsr(_mm_getcsr() | 0x8040);
#elif defined(__aarch64__)
    uint64_t fpcr;
    __asm__ __volatile__("mrs %0, fpcr" : "=r"(fpcr));
    __asm__ __volatile__("msr fpcr, %0" : : "r"(fpcr | (1ULL << 24)));
#endif
}

static const int MAXV = 8;    // voices
static const int MAXP = 10;   // params per module
static const int MAXIO = 8;   // jacks per side
static const int SCOPE = 8192;
static const float PI = 3.14159265358979f;
static const float C4 = 261.6256f;

static inline float clampf(float x, float a, float b) { return x < a ? a : (x > b ? b : x); }
// Cheap tanh-shaped saturator: unity gain for small signals, limits at +-limit.
static inline float sat(float x, float limit) {
    x = clampf(x / limit, -3.0f, 3.0f);
    return limit * x * (27 + x * x) / (27 + 9 * x * x);
}
// 2^x, accurate to a few millionths (well under a hundredth of a cent).
static inline float fexp2(float x) {
    x = clampf(x, -40.0f, 40.0f);
    float r = std::nearbyint(x), f = (x - r) * 0.69314718f;
    float p = 1 + f * (1 + f * (0.5f + f * (0.16666667f + f * (0.041666667f + f * 0.0083333333f))));
    return std::ldexp(p, (int)r);
}
// One-pole coefficient for a time constant, recomputed only when the time changes.
struct Coef {
    float last = -1, c = 1;
    float get(float seconds, float sr) {
        if (seconds != last) { last = seconds; c = 1.0f - std::exp(-1.0f / (std::max(seconds, 1e-4f) * sr)); }
        return c;
    }
};

struct Rng {
    uint32_t s = 22222;
    float next() { s = s * 1664525u + 1013904223u; return (int32_t)s * (1.0f / 2147483648.0f); }
};

struct Voice {
    float gl = 0, target = 0;   // glided pitch and where it is heading
    float pitch = 0, gate = 0, vel = 0.8f;
    int note = -1, retrig = 0;
    bool held = false;
    uint64_t age = 0;
};

struct Shared {
    float mod = 0;
    // Transport clock, valid for the current sample: `tick` on the first sample
    // of each 16th-note step, `start` when the transport starts playing.
    bool tick = false, start = false;
    float stepSamples = 6000;
};

struct Module {
    float in[MAXIO] = {}, out[MAXIO] = {};
    bool conn[MAXIO] = {};      // which inputs have a cable
    bool used[MAXIO] = {};      // which outputs have a cable
    const float* p = nullptr;   // smoothed params, shared by all voices
    const Voice* v = nullptr;
    const Shared* g = nullptr;
    float sr = 48000;
    Rng rng;
    virtual ~Module() {}
    virtual void init() {}
    virtual void tick() = 0;
};

// ---------------------------------------------------------------- modules

// 921-style oscillator: all four waveforms at once, polyBLEP anti-aliasing,
// hard sync, and a little pitch drift so stacked oscillators beat.
struct VCO : Module {
    double ph = 0;
    float drift = 0, lastSync = 0;
    static float blep(float t, float dt) {
        if (t < dt) { float x = t / dt; return x + x - x * x - 1; }
        if (t > 1 - dt) { float x = (t - 1) / dt; return x * x + x + x + 1; }
        return 0;
    }
    void tick() override {
        int range = (int)std::lround(p[0]);
        float oct = range == 0 ? -9.0f : (float)(range - 3);
        drift = drift * 0.99995f + 0.00002f * rng.next();
        float fm = p[4] * p[4] * p[4] * 5.0f;
        float pitch = oct + (p[1] + p[2] * 0.01f) / 12.0f + in[0] + in[1] * fm + drift;
        if (p[6] > 0.5f) pitch += v->pitch;
        float dt = clampf(C4 * fexp2(pitch) / sr, 0.0f, 0.45f);
        if (conn[3]) {
            if (in[3] > 0 && lastSync <= 0) ph = 0;
            lastSync = in[3];
        }
        ph += dt;
        if (ph >= 1) ph -= 1;
        float t = (float)ph;
        float pw = clampf(p[3] + in[2] * p[5] * 0.5f, 0.03f, 0.97f);
        float t2 = t + 1 - pw;
        if (t2 >= 1) t2 -= 1;
        if (used[0]) out[0] = std::sin(2 * PI * t);
        out[1] = 1 - 4 * std::fabs(t - 0.5f);
        out[2] = 2 * t - 1 - blep(t, dt);
        out[3] = (t < pw ? 1.0f : -1.0f) + blep(t, dt) - blep(t2, dt) - (2 * pw - 1);   // no DC
    }
};

// 904A-style transistor ladder: four zero-delay one-pole stages with
// saturating feedback, run at 2x. Self-oscillates at full resonance.
struct VCF : Module {
    float s[4] = {};
    void tick() override {
        float cv = (in[1] * p[3] + in[2] * p[4]) * 8.0f + v->pitch * p[5];
        float fc = clampf(p[0] * fexp2(cv), 10.0f, std::min(22000.0f, sr * 0.45f));
        float w = PI * fc / (2 * sr), w2 = w * w;
        float g = w * (15 - w2) / (15 - 6 * w2);   // tan(w), Pade approximation
        float G = g / (1 + g);
        float k = p[1] * 4.6f;
        float x = in[0] * p[2] + 1e-5f * rng.next();
        float y[5] = {};
        for (int os = 0; os < 2; os++) {
            float S = (1 - G) * (G * G * G * s[0] + G * G * s[1] + G * s[2] + s[3]);
            y[0] = sat((x - k * S) / (1 + k * G * G * G * G), 1.4f);
            for (int i = 0; i < 4; i++) {
                float d = (y[i] - s[i]) * G;
                y[i + 1] = d + s[i];
                s[i] = y[i + 1] + d;
            }
        }
        if (!(std::fabs(y[4]) < 100)) { s[0] = s[1] = s[2] = s[3] = 0; y[4] = 0; }
        float o;
        switch ((int)std::lround(p[6])) {
            case 1: o = y[2]; break;
            case 2: o = 2 * (y[1] - y[2]); break;
            case 3: o = y[0] - 4 * y[1] + 6 * y[2] - 4 * y[3] + y[4]; break;
            default: o = y[4];
        }
        out[0] = o * (1 + 0.15f * k);
    }
};

// 911-style envelope with RC-shaped segments. Follows the keyboard gate
// unless something is patched into GATE.
struct ENV : Module {
    int stage = 0;
    float e = 0, lastGate = 0, scale = 1;
    Coef ca, cd, cr;
    void tick() override {
        float gate = conn[0] ? in[0] : v->gate;
        bool on = gate > 0.5f;
        if (on && lastGate <= 0.5f) { stage = 1; scale = 1 - p[4] + p[4] * v->vel; }
        if (!on) stage = 0;
        lastGate = gate;
        if (stage == 1) {
            e += (1.3f - e) * ca.get(p[0] / 1.47f, sr);
            if (e >= 1) { e = 1; stage = 2; }
        } else if (stage == 2) {
            e += (p[2] - e) * cd.get(p[1] / 3, sr);
        } else {
            e -= e * cr.get(p[3] / 3, sr);
        }
        out[0] = e * scale;
        out[1] = -out[0];
    }
};

struct VCA : Module {
    void tick() override {
        float gain = clampf(p[0] + (conn[1] ? in[1] * p[1] : 0), 0, 2);
        if (p[2] > 0.5f) gain *= gain;
        out[0] = in[0] * gain;
    }
};

struct LFO : Module {
    float ph = 0, fade = 1, lastReset = 0, lastGate = 0;
    void tick() override {
        if (v->gate > 0.5f && lastGate <= 0.5f && p[1] > 0.01f) fade = 0;
        lastGate = v->gate;
        if (fade < 1) fade = std::min(1.0f, fade + 1.0f / (std::max(p[1], 0.01f) * sr));
        if (in[1] > 0 && lastReset <= 0) ph = 0;
        lastReset = in[1];
        ph += p[0] * (conn[0] ? fexp2(in[0] * 2) : 1.0f) / sr;
        if (ph >= 1) ph -= std::floor(ph);
        if (used[0]) out[0] = std::sin(2 * PI * ph) * fade;
        out[1] = (1 - 4 * std::fabs(ph - 0.5f)) * fade;
        out[2] = (2 * ph - 1) * fade;
        out[3] = (ph < 0.5f ? 1.0f : -1.0f) * fade;
    }
};

struct Noise : Module {
    float b0 = 0, b1 = 0, b2 = 0;
    void tick() override {
        float w = rng.next();
        b0 = 0.99765f * b0 + w * 0.0990460f;
        b1 = 0.96300f * b1 + w * 0.2965164f;
        b2 = 0.57000f * b2 + w * 1.0526913f;
        out[0] = w * p[0];
        out[1] = (b0 + b1 + b2 + w * 0.1848f) * 0.25f * p[0];
    }
};

// CP3-style mixer: overdrives gently when pushed.
struct Mixer : Module {
    void tick() override {
        float s = in[0] * p[0] + in[1] * p[1] + in[2] * p[2] + in[3] * p[3];
        out[0] = sat(s, 2.0f);
        out[1] = -out[0];
    }
};

// Sample & hold. Samples noise on its own clock unless IN / TRIG are patched.
struct SampleHold : Module {
    float ph = 0, held = 0, o = 0, lastTrig = 0;
    Coef slew;
    void tick() override {
        bool trig;
        if (conn[1]) {
            trig = in[1] > 0 && lastTrig <= 0;
            lastTrig = in[1];
        } else {
            ph += p[0] / sr;
            trig = ph >= 1;
            if (trig) ph -= 1;
        }
        float src = conn[0] ? in[0] : rng.next();
        if (trig) held = src;
        o += (held - o) * (p[1] < 0.002f ? 1.0f : slew.get(p[1] * 0.3f, sr));
        out[0] = o;
    }
};

// Tape-style echo: delay time glides like a tape head, repeats get darker.
struct Delay : Module {
    std::vector<float> buf;
    int w = 0;
    float t = 0.3f, lp = 0, lastTone = -1, tone = 1;
    void init() override { buf.assign((size_t)(sr * 2) + 4, 0.0f); }
    void tick() override {
        int n = (int)buf.size();
        float target = clampf(p[0] * (conn[1] ? fexp2(in[1]) : 1.0f), 0.005f, 1.95f);
        t += (target - t) * 0.0004f;
        float pos = w - t * sr;
        while (pos < 0) pos += n;
        int i0 = (int)pos;
        float f = pos - i0;
        float rd = buf[i0 % n] * (1 - f) + buf[(i0 + 1) % n] * f;
        if (p[2] != lastTone) { lastTone = p[2]; tone = 1 - std::exp(-2 * PI * 500.0f * std::exp2(p[2] * 5) / sr); }
        lp += (rd - lp) * tone;
        if (!(std::fabs(lp) < 100)) lp = 0;
        buf[w] = in[0] + sat(lp * p[1], 1.5f);
        w = (w + 1) % n;
        out[0] = in[0] * (1 - p[3]) + rd * p[3];
    }
};

// Comb/allpass reverb standing in for the 905 spring tank.
struct Reverb : Module {
    std::vector<float> cb[6], ab[3];
    int ci[6] = {}, ai[3] = {};
    float cl[6] = {};
    void init() override {
        static const int cs[6] = {1116, 1188, 1277, 1356, 1422, 1491}, as[3] = {556, 441, 341};
        for (int i = 0; i < 6; i++) cb[i].assign((size_t)(cs[i] * sr / 44100), 0.0f);
        for (int i = 0; i < 3; i++) ab[i].assign((size_t)(as[i] * sr / 44100), 0.0f);
    }
    void tick() override {
        float x = in[0], fb = 0.72f + 0.26f * p[0], damp = 0.05f + 0.6f * (1 - p[2]), s = 0;
        for (int c = 0; c < 6; c++) {
            float y = cb[c][ci[c]];
            cl[c] = y * (1 - damp) + cl[c] * damp;
            cb[c][ci[c]] = x * 0.03f + cl[c] * fb;
            if (++ci[c] >= (int)cb[c].size()) ci[c] = 0;
            s += y;
        }
        for (int a = 0; a < 3; a++) {
            float b = ab[a][ai[a]], y = b - s;
            ab[a][ai[a]] = s + b * 0.5f;
            if (++ai[a] >= (int)ab[a].size()) ai[a] = 0;
            s = y;
        }
        if (!(std::fabs(s) < 100)) s = 0;
        out[0] = x * (1 - p[1]) + s * p[1];
    }
};

// 960-style step sequencer: eight tuned steps. Follows the transport's 16th
// notes unless a clock is patched in.
struct Seq8 : Module {
    int pos = -1;
    float lastClk = 0, lastRst = 0, gateLeft = 0;
    void tick() override {
        int len = std::max(1, std::min(8, (int)std::lround(p[8])));
        bool adv;
        if (conn[0]) { adv = in[0] > 0 && lastClk <= 0; lastClk = in[0]; }
        else adv = g->tick;
        if (g->start || (in[1] > 0 && lastRst <= 0)) pos = -1;
        lastRst = in[1];
        if (adv) { pos = (pos + 1) % len; gateLeft = g->stepSamples * 0.5f; }
        out[0] = std::lround(p[pos < 0 ? 0 : pos % len]) / 12.0f;
        if (conn[0]) out[1] = (pos >= 0 && in[0] > 0) ? 1.0f : 0.0f;
        else { out[1] = gateLeft > 0 ? 1.0f : 0.0f; if (gateLeft > 0) gateLeft -= 1; }
    }
};

struct KbdCV : Module {
    void tick() override {
        out[0] = v->pitch;
        out[1] = v->gate;
        out[2] = v->vel;
        out[3] = g->mod;
    }
};

// Final output. out[0]/out[1] are picked up by the engine as left/right.
struct Output : Module {
    void tick() override {
        if (conn[1]) {
            out[0] = in[0] * p[0];
            out[1] = in[1] * p[0];
        } else {
            float a = (p[1] + 1) * PI / 4;
            out[0] = in[0] * p[0] * std::cos(a) * 1.4142f;
            out[1] = in[0] * p[0] * std::sin(a) * 1.4142f;
        }
    }
};

// ------------------------------------------------------------ descriptors

enum { LIN = 0, EXP = 1, STEP = 2 };
struct PDesc { const char* name; float mn, mx, def; int curve; const char* opts; const char* unit; };
struct MDesc {
    const char* type; const char* title; const char* model; int cols;
    std::vector<PDesc> params;
    std::vector<const char*> ins, outs;
    Module* (*make)();
    bool poly = true;   // false: one shared instance fed by the sum of all voices
};
template <class T> static Module* mk() { return new T(); }

static const std::vector<MDesc>& descs() {
    static const std::vector<MDesc> d = {
        {"VCO", "OSCILLATOR", "921", 3,
         {{"RANGE", 0, 5, 3, STEP, "LO|32'|16'|8'|4'|2'", ""}, {"TUNE", -12, 12, 0, LIN, "", "st"},
          {"FINE", -50, 50, 0, LIN, "", "ct"}, {"WIDTH", 0.05f, 0.95f, 0.5f, LIN, "", "%"},
          {"FM AMT", 0, 1, 0, LIN, "", ""}, {"PWM AMT", 0, 1, 0, LIN, "", ""},
          {"KBD", 0, 1, 1, STEP, "OFF|ON", ""}},
         {"V/OCT", "FM", "PWM", "SYNC"}, {"SINE", "TRI", "SAW", "PULSE"}, mk<VCO>},
        {"VCF", "LADDER FILTER", "904A", 3,
         {{"CUTOFF", 20, 20000, 1500, EXP, "", "Hz"}, {"RESONANCE", 0, 1, 0.2f, LIN, "", ""},
          {"DRIVE", 0.2f, 4, 1, EXP, "", "x"}, {"CV1 AMT", -1, 1, 0, LIN, "", ""},
          {"CV2 AMT", -1, 1, 0, LIN, "", ""}, {"KEY TRACK", 0, 1, 0.5f, LIN, "", ""},
          {"MODE", 0, 3, 0, STEP, "LP24|LP12|BP|HP", ""}},
         {"IN", "CV1", "CV2"}, {"OUT"}, mk<VCF>},
        {"ENV", "ENVELOPE", "911", 2,
         {{"ATTACK", 0.001f, 10, 0.005f, EXP, "", "s"}, {"DECAY", 0.001f, 10, 0.3f, EXP, "", "s"},
          {"SUSTAIN", 0, 1, 0.6f, LIN, "", ""}, {"RELEASE", 0.001f, 10, 0.3f, EXP, "", "s"},
          {"VELOCITY", 0, 1, 0, LIN, "", ""}},
         {"GATE"}, {"ENV", "INV"}, mk<ENV>},
        {"VCA", "AMPLIFIER", "902", 2,
         {{"GAIN", 0, 1, 0, LIN, "", ""}, {"CV AMT", 0, 1, 1, LIN, "", ""},
          {"RESPONSE", 0, 1, 0, STEP, "LIN|EXP", ""}},
         {"IN", "CV"}, {"OUT"}, mk<VCA>},
        {"LFO", "LOW FREQ OSC", "LFO", 2,
         {{"RATE", 0.02f, 50, 5, EXP, "", "Hz"}, {"FADE IN", 0, 5, 0, LIN, "", "s"}},
         {"RATE", "RESET"}, {"SINE", "TRI", "SAW", "SQR"}, mk<LFO>},
        {"NOISE", "NOISE", "903A", 2,
         {{"LEVEL", 0, 1, 1, LIN, "", ""}}, {}, {"WHITE", "PINK"}, mk<Noise>},
        {"MIXER", "MIXER", "CP3", 2,
         {{"LEVEL 1", 0, 1.5f, 0.7f, LIN, "", ""}, {"LEVEL 2", 0, 1.5f, 0.7f, LIN, "", ""},
          {"LEVEL 3", 0, 1.5f, 0.7f, LIN, "", ""}, {"LEVEL 4", 0, 1.5f, 0.7f, LIN, "", ""}},
         {"IN 1", "IN 2", "IN 3", "IN 4"}, {"OUT", "INV"}, mk<Mixer>},
        {"SH", "SAMPLE & HOLD", "S&H", 2,
         {{"RATE", 0.1f, 50, 6, EXP, "", "Hz"}, {"SLEW", 0, 1, 0, LIN, "", ""}},
         {"IN", "TRIG"}, {"OUT"}, mk<SampleHold>},
        {"DELAY", "TAPE ECHO", "ECHO", 2,
         {{"TIME", 0.02f, 1.5f, 0.35f, EXP, "", "s"}, {"FEEDBACK", 0, 0.95f, 0.4f, LIN, "", ""},
          {"TONE", 0, 1, 0.6f, LIN, "", ""}, {"MIX", 0, 1, 0.3f, LIN, "", ""}},
         {"IN", "TIME"}, {"OUT"}, mk<Delay>, false},
        {"REVERB", "REVERB", "905", 2,
         {{"DECAY", 0, 1, 0.5f, LIN, "", ""}, {"MIX", 0, 1, 0.25f, LIN, "", ""},
          {"TONE", 0, 1, 0.6f, LIN, "", ""}},
         {"IN"}, {"OUT"}, mk<Reverb>, false},
        {"SEQ", "SEQUENCER", "960", 4,
         {{"STEP 1", -24, 24, 0, STEP, "", "st"}, {"STEP 2", -24, 24, 0, STEP, "", "st"},
          {"STEP 3", -24, 24, 0, STEP, "", "st"}, {"STEP 4", -24, 24, 0, STEP, "", "st"},
          {"STEP 5", -24, 24, 0, STEP, "", "st"}, {"STEP 6", -24, 24, 0, STEP, "", "st"},
          {"STEP 7", -24, 24, 0, STEP, "", "st"}, {"STEP 8", -24, 24, 0, STEP, "", "st"},
          {"LENGTH", 1, 8, 8, STEP, "", ""}},
         {"CLOCK", "RESET"}, {"CV", "GATE"}, mk<Seq8>, false},
        {"KBD", "KEYBOARD CV", "950", 2, {}, {}, {"PITCH", "GATE", "VEL", "MOD"}, mk<KbdCV>},
        {"OUT", "OUTPUT", "MAIN", 2,
         {{"LEVEL", 0, 1, 0.7f, LIN, "", ""}, {"PAN", -1, 1, 0, LIN, "", ""}},
         {"LEFT", "RIGHT"}, {}, mk<Output>, false},
    };
    return d;
}

// ----------------------------------------------------------------- engine

struct Slot;
struct Conn { Slot* src; int o, i; };
struct Slot {
    int id = 0, type = 0, np = 0, nin = 0;
    bool isOut = false, mark = false, poly = true;
    std::atomic<float> target[MAXP];
    float cur[MAXP] = {};
    bool stepped[MAXP] = {};
    std::unique_ptr<Module> inst[MAXV];
    std::vector<Conn> conns;
};
struct Cable { int s, o, d, i; };
struct Ev { int type; int a; float b; };
enum { EV_ON, EV_OFF, EV_BEND, EV_MOD, EV_ALLOFF };

struct Engine {
    float sr;
    std::mutex mx;
    std::vector<std::unique_ptr<Slot>> slots;
    std::vector<Cable> cables;
    std::vector<Slot*> order;
    int nextId = 1;

    Shared shared;
    Voice voices[MAXV];
    int nv = 1;
    std::vector<int> stack;   // held notes, mono mode
    uint64_t ageCtr = 0;
    float bend = 0;
    std::atomic<float> master{0.7f}, glide{0}, bendRange{2}, legato{1};

    // Pattern sequencer: 16th-note steps that play the rack like a keyboard.
    // A step is note | on<<8 | accent<<9 | slide<<10.
    static const int STEPS = 16;
    std::atomic<int> steps[STEPS];
    std::atomic<float> bpm{120}, swing{0}, gateLen{0.5f}, seqLen{16};
    std::atomic<int> playing{0}, seqPos{-1};
    bool wasPlaying = false, sliding = false, forceGlide = false;
    int stepIdx = -1, seqNote = -1;
    double untilStep = 0;
    float gateLeft = 0;

    Ev q[1024];
    std::atomic<unsigned> qr{0}, qw{0};
    std::mutex qmx;

    float scope[SCOPE] = {};
    float dcx[2] = {}, dcy[2] = {};   // output DC blocker state
    std::atomic<unsigned> sw{0};
    std::atomic<float> peak{0}, cpu{0};

    explicit Engine(float rate) : sr(rate) { for (auto& s : steps) s.store(57); }

    Slot* find(int id) {
        for (auto& s : slots) if (s->id == id) return s.get();
        return nullptr;
    }

    void visit(Slot* s) {
        if (s->mark) return;
        s->mark = true;
        for (auto& c : s->conns) visit(c.src);
        order.push_back(s);
    }

    // Recompute per-module inputs and a sources-first processing order.
    // Feedback loops are allowed; they just pick up a one-sample delay.
    void rebuild() {
        for (auto& s : slots) {
            s->conns.clear();
            s->mark = false;
            for (auto& m : s->inst)
                if (m) { std::fill(m->conn, m->conn + MAXIO, false); std::fill(m->used, m->used + MAXIO, false); }
        }
        for (auto& c : cables) {
            Slot *src = find(c.s), *dst = find(c.d);
            if (!src || !dst) continue;
            dst->conns.push_back({src, c.o, c.i});
            for (auto& m : dst->inst) if (m) m->conn[c.i] = true;
            for (auto& m : src->inst) if (m) m->used[c.o] = true;
        }
        order.clear();
        for (auto& s : slots) visit(s.get());
    }

    void push(int type, int a, float b) {
        std::lock_guard<std::mutex> l(qmx);
        unsigned w = qw.load();
        if (w - qr.load() >= 1024) return;
        q[w % 1024] = {type, a, b};
        qw.store(w + 1);
    }

    void allOff() {
        stack.clear();
        for (auto& v : voices) { v.held = false; v.note = -1; }
    }

    void noteOn(int note, float vel) {
        float target = (note - 60) / 12.0f;
        bool noGlide = glide.load() < 0.002f && !forceGlide;
        if (nv == 1) {
            Voice& v = voices[0];
            stack.erase(std::remove(stack.begin(), stack.end(), note), stack.end());
            stack.push_back(note);
            if (v.held && legato.load() < 0.5f) v.retrig = (int)(sr * 0.002f);
            if (noGlide) v.gl = target;
            v.target = target; v.note = note; v.vel = vel; v.held = true;
            return;
        }
        Voice* pick = nullptr;
        for (int i = 0; i < nv; i++) if (voices[i].note == note) pick = &voices[i];
        if (!pick) {
            for (int i = 0; i < nv; i++) {
                Voice& v = voices[i];
                if (!pick || (!v.held && pick->held) || (v.held == pick->held && v.age < pick->age)) pick = &v;
            }
        }
        if (pick->held) pick->retrig = (int)(sr * 0.002f);
        if (noGlide) pick->gl = target;
        pick->target = target; pick->note = note; pick->vel = vel; pick->held = true;
        pick->age = ++ageCtr;
    }

    void noteOff(int note) {
        if (nv == 1) {
            stack.erase(std::remove(stack.begin(), stack.end(), note), stack.end());
            Voice& v = voices[0];
            if (stack.empty()) { v.held = false; return; }
            v.note = stack.back();
            v.target = (v.note - 60) / 12.0f;
            if (glide.load() < 0.002f) v.gl = v.target;
            return;
        }
        for (int i = 0; i < nv; i++) if (voices[i].note == note) voices[i].held = false;
    }

    void drain() {
        unsigned r = qr.load(), w = qw.load();
        for (; r != w; r++) {
            Ev& e = q[r % 1024];
            switch (e.type) {
                case EV_ON: noteOn(e.a, e.b); break;
                case EV_OFF: noteOff(e.a); break;
                case EV_BEND: bend = e.b; break;
                case EV_MOD: shared.mod = e.b; break;
                case EV_ALLOFF: allOff(); break;
            }
        }
        qr.store(r);
    }

    // Advance the transport by one sample, starting and ending pattern notes.
    void sequence() {
        shared.tick = shared.start = false;
        if (!playing.load(std::memory_order_relaxed)) {
            if (wasPlaying) {
                wasPlaying = sliding = false;
                if (seqNote >= 0) noteOff(seqNote);
                seqNote = -1;
                seqPos.store(-1);
            }
            return;
        }
        if (!wasPlaying) { wasPlaying = true; stepIdx = -1; untilStep = 0; shared.start = true; }
        if (untilStep <= 0) {
            int len = std::max(1, std::min(STEPS, (int)seqLen.load()));
            stepIdx = (stepIdx + 1) % len;
            float sw = clampf(swing.load(), 0, 0.7f);
            float dur = sr * 15.0f / clampf(bpm.load(), 20, 400) * (stepIdx % 2 ? 1 - sw : 1 + sw);
            untilStep += dur;
            shared.tick = true;
            shared.stepSamples = dur;
            seqPos.store(stepIdx);
            int st = steps[stepIdx].load(), note = st & 127, prev = seqNote;
            if (st & 0x100) {
                // Arriving from a slid step: overlap the notes and glide, 303-style.
                forceGlide = sliding;
                noteOn(note, st & 0x200 ? 1.0f : 0.65f);
                forceGlide = false;
                if (prev >= 0 && prev != note) noteOff(prev);
                seqNote = note;
                sliding = st & 0x400;
                gateLeft = sliding ? dur * 2 : std::max(32.0f, dur * clampf(gateLen.load(), 0.05f, 1.0f) - 32);
            } else {
                if (prev >= 0) noteOff(prev);
                seqNote = -1;
                sliding = false;
            }
        }
        untilStep -= 1;
        if (seqNote >= 0 && (gateLeft -= 1) <= 0) { noteOff(seqNote); seqNote = -1; sliding = false; }
    }

    // Caller holds mx. Writes interleaved stereo.
    void process(float* out, int frames) {
        drain();
        float gt = glide.load();
        float gc = gt < 0.002f ? 1.0f : 1.0f - std::exp(-1.0f / (gt * 0.3f * sr));
        float gcSlide = 1.0f - std::exp(-1.0f / (0.02f * sr));
        float bendOct = bend * bendRange.load() / 12.0f;
        float vol = master.load();
        float smooth = 1.0f - std::exp(-1.0f / (0.004f * sr));
        float pk = 0;
        unsigned w = sw.load();
        for (int n = 0; n < frames; n++) {
            sequence();
            float glideNow = (sliding && gc >= 1) ? gcSlide : gc;
            for (int vi = 0; vi < nv; vi++) {
                Voice& v = voices[vi];
                v.gl += (v.target - v.gl) * glideNow;
                v.pitch = v.gl + bendOct;
                if (v.retrig > 0) { v.retrig--; v.gate = 0; }
                else v.gate = v.held ? 1.0f : 0.0f;
            }
            for (auto& s : slots)
                for (int k = 0; k < s->np; k++) {
                    float t = s->target[k].load(std::memory_order_relaxed);
                    s->cur[k] = s->stepped[k] ? t : s->cur[k] + (t - s->cur[k]) * smooth;
                }
            float L = 0, R = 0;
            for (Slot* s : order) {
                int count = s->poly ? nv : 1;
                for (int vi = 0; vi < count; vi++) {
                    Module* m = s->inst[vi].get();
                    for (int i = 0; i < s->nin; i++) m->in[i] = 0;
                    for (auto& c : s->conns) {
                        if (!c.src->poly) m->in[c.i] += c.src->inst[0]->out[c.o];
                        else if (s->poly) m->in[c.i] += c.src->inst[vi]->out[c.o];
                        else for (int k = 0; k < nv; k++) m->in[c.i] += c.src->inst[k]->out[c.o];
                    }
                    m->tick();
                    if (s->isOut) { L += m->out[0]; R += m->out[1]; }
                }
            }
            // AC-couple the output (5 Hz high-pass), like any real amp input.
            dcy[0] = L - dcx[0] + 0.99935f * dcy[0]; dcx[0] = L; L = dcy[0] * vol;
            dcy[1] = R - dcx[1] + 0.99935f * dcy[1]; dcx[1] = R; R = dcy[1] * vol;
            if (!std::isfinite(L) || !std::isfinite(R)) L = R = 0;
            // Soft limiter: clean below half scale, never exceeds full scale.
            float aL = std::fabs(L), aR = std::fabs(R);
            if (aL > 0.5f) L = std::copysign(0.5f + 0.5f * std::tanh((aL - 0.5f) * 2), L);
            if (aR > 0.5f) R = std::copysign(0.5f + 0.5f * std::tanh((aR - 0.5f) * 2), R);
            out[2 * n] = L;
            out[2 * n + 1] = R;
            scope[w++ % SCOPE] = 0.5f * (L + R);
            pk = std::max(pk, std::max(std::fabs(L), std::fabs(R)));
        }
        sw.store(w);
        if (pk > peak.load()) peak.store(pk);
    }
};

// ------------------------------------------------- PortAudio (no headers)

extern "C" {
typedef void PaStream;
typedef int PaCallback(const void*, void*, unsigned long, const void*, unsigned long, void*);
int Pa_Initialize(void);
int Pa_Terminate(void);
int Pa_OpenDefaultStream(PaStream**, int, int, unsigned long, double, unsigned long, PaCallback*, void*);
int Pa_StartStream(PaStream*);
int Pa_StopStream(PaStream*);
int Pa_CloseStream(PaStream*);
const char* Pa_GetErrorText(int);
}

static PaStream* g_stream = nullptr;
static std::string g_err;

static int audioCb(const void*, void* out, unsigned long frames, const void*, unsigned long, void* user) {
    flushDenormals();
    Engine* e = (Engine*)user;
    auto t0 = std::chrono::steady_clock::now();
    std::unique_lock<std::mutex> l(e->mx, std::try_to_lock);
    if (l.owns_lock()) e->process((float*)out, (int)frames);
    else std::memset(out, 0, frames * 2 * sizeof(float));
    std::chrono::duration<float> dt = std::chrono::steady_clock::now() - t0;
    float load = dt.count() / (frames / e->sr);
    e->cpu.store(e->cpu.load() * 0.9f + load * 0.1f);
    return 0;
}

// ------------------------------------------------------------------ C API

extern "C" {

Engine* sy_create(float sr) { return new Engine(sr); }

const char* sy_describe() {
    static std::string js;
    if (!js.empty()) return js.c_str();
    char b[256];
    js = "[";
    for (auto& d : descs()) {
        if (js.size() > 1) js += ",";
        js += std::string("{\"type\":\"") + d.type + "\",\"title\":\"" + d.title + "\",\"model\":\"" + d.model + "\"";
        std::snprintf(b, sizeof b, ",\"cols\":%d,\"params\":[", d.cols);
        js += b;
        for (size_t i = 0; i < d.params.size(); i++) {
            auto& p = d.params[i];
            std::snprintf(b, sizeof b, "%s{\"name\":\"%s\",\"min\":%g,\"max\":%g,\"def\":%g,\"curve\":%d,\"opts\":\"%s\",\"unit\":\"%s\"}",
                          i ? "," : "", p.name, p.mn, p.mx, p.def, p.curve, p.opts, p.unit);
            js += b;
        }
        js += "],\"ins\":[";
        for (size_t i = 0; i < d.ins.size(); i++) js += std::string(i ? "," : "") + "\"" + d.ins[i] + "\"";
        js += "],\"outs\":[";
        for (size_t i = 0; i < d.outs.size(); i++) js += std::string(i ? "," : "") + "\"" + d.outs[i] + "\"";
        js += "]}";
    }
    js += "]";
    return js.c_str();
}

int sy_add_module(Engine* e, const char* type) {
    auto& ds = descs();
    for (size_t t = 0; t < ds.size(); t++) {
        if (std::strcmp(ds[t].type, type)) continue;
        auto s = std::make_unique<Slot>();
        s->type = (int)t;
        s->np = (int)ds[t].params.size();
        s->nin = (int)ds[t].ins.size();
        s->isOut = !std::strcmp(type, "OUT");
        s->poly = ds[t].poly;
        for (int k = 0; k < MAXP; k++) {
            float d = k < s->np ? ds[t].params[k].def : 0;
            s->target[k].store(d);
            s->cur[k] = d;
            s->stepped[k] = k < s->np && ds[t].params[k].curve == STEP;
        }
        std::lock_guard<std::mutex> l(e->mx);
        s->id = e->nextId++;
        for (int v = 0; v < (s->poly ? MAXV : 1); v++) {
            Module* m = ds[t].make();
            m->p = s->cur; m->v = &e->voices[v]; m->g = &e->shared; m->sr = e->sr;
            m->rng.s = 12345u + 7919u * (unsigned)s->id + 104729u * (unsigned)v;
            m->init();
            s->inst[v].reset(m);
        }
        int id = s->id;
        e->slots.push_back(std::move(s));
        e->rebuild();
        return id;
    }
    return -1;
}

void sy_remove_module(Engine* e, int id) {
    std::lock_guard<std::mutex> l(e->mx);
    auto& c = e->cables;
    c.erase(std::remove_if(c.begin(), c.end(), [&](const Cable& k) { return k.s == id || k.d == id; }), c.end());
    auto& s = e->slots;
    s.erase(std::remove_if(s.begin(), s.end(), [&](const std::unique_ptr<Slot>& k) { return k->id == id; }), s.end());
    e->rebuild();
}

void sy_clear(Engine* e) {
    std::lock_guard<std::mutex> l(e->mx);
    e->cables.clear();
    e->slots.clear();
    e->rebuild();
    e->allOff();
}

void sy_set_param(Engine* e, int id, int index, float value) {
    Slot* s = e->find(id);
    if (s && index >= 0 && index < s->np) s->target[index].store(value);
}

void sy_connect(Engine* e, int src, int o, int dst, int i, int on) {
    std::lock_guard<std::mutex> l(e->mx);
    auto& c = e->cables;
    c.erase(std::remove_if(c.begin(), c.end(),
                           [&](const Cable& k) { return k.s == src && k.o == o && k.d == dst && k.i == i; }),
            c.end());
    if (on && o >= 0 && o < MAXIO && i >= 0 && i < MAXIO) c.push_back({src, o, dst, i});
    e->rebuild();
}

void sy_set_voices(Engine* e, int n) {
    std::lock_guard<std::mutex> l(e->mx);
    e->allOff();
    e->nv = std::max(1, std::min(MAXV, n));
}

// 0 master volume, 1 glide seconds, 2 bend range (semitones), 3 legato,
// 4 tempo (bpm), 5 swing (0..0.7), 6 gate length (fraction of a step), 7 pattern length
void sy_set_global(Engine* e, int which, float value) {
    if (which == 0) e->master.store(value);
    else if (which == 1) e->glide.store(value);
    else if (which == 2) e->bendRange.store(value);
    else if (which == 3) e->legato.store(value);
    else if (which == 4) e->bpm.store(value);
    else if (which == 5) e->swing.store(value);
    else if (which == 6) e->gateLen.store(value);
    else if (which == 7) e->seqLen.store(value);
}

void sy_seq_step(Engine* e, int index, int note, int on, int accent, int slide) {
    if (index < 0 || index >= Engine::STEPS) return;
    e->steps[index].store((note & 127) | (on ? 0x100 : 0) | (accent ? 0x200 : 0) | (slide ? 0x400 : 0));
}
void sy_transport(Engine* e, int play) { e->playing.store(play); }
int sy_seq_pos(Engine* e) { return e->seqPos.load(); }

void sy_note_on(Engine* e, int note, float vel) { e->push(EV_ON, note, vel); }
void sy_note_off(Engine* e, int note) { e->push(EV_OFF, note, 0); }
void sy_bend(Engine* e, float v) { e->push(EV_BEND, 0, v); }
void sy_mod(Engine* e, float v) { e->push(EV_MOD, 0, v); }
void sy_all_off(Engine* e) { e->push(EV_ALLOFF, 0, 0); }

// Offline / pull rendering: interleaved stereo float32.
void sy_render(Engine* e, float* buf, int frames) {
    flushDenormals();
    std::lock_guard<std::mutex> l(e->mx);
    e->process(buf, frames);
}

void sy_get_scope(Engine* e, float* dst, int n) {
    unsigned w = e->sw.load();
    for (int i = 0; i < n; i++) dst[i] = e->scope[(w - n + i) % SCOPE];
}
float sy_get_peak(Engine* e) { return e->peak.exchange(0); }
float sy_get_cpu(Engine* e) { return e->cpu.load(); }

int sy_start_audio(Engine* e, int frames) {
    int err = Pa_Initialize();
    if (!err) err = Pa_OpenDefaultStream(&g_stream, 0, 2, 1 /* paFloat32 */, e->sr, frames, audioCb, e);
    if (!err) err = Pa_StartStream(g_stream);
    if (err) { g_err = Pa_GetErrorText(err); g_stream = nullptr; }
    return err;
}
const char* sy_audio_error() { return g_err.c_str(); }
void sy_stop_audio() {
    if (!g_stream) return;
    Pa_StopStream(g_stream);
    Pa_CloseStream(g_stream);
    Pa_Terminate();
    g_stream = nullptr;
}

}  // extern "C"
