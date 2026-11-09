// The native sound mixer interpolates voices with the SPU2's 4-tap Gaussian filter: a steady level stays steady,
// every phase sums to unity gain, and a Nyquist-rate alternation is strongly attenuated (Catmull-Rom passes it).
#include "../runtime/native_sound/bozo/src/mixer.cpp"
#include "../runtime/native_sound/bozo/src/port_stream.cpp"
#include <cstdio>

int main()
{
    using bozo::audio::interpolate;
    int failures = 0;
    for (uint32_t t = 0; t < 0x1000u; t += 16u)
    {
        const int32_t steady = interpolate(20000, 20000, 20000, 20000, t);
        if (steady < 19900 || steady > 20000) { std::printf("phase %u steady %d\n", t, steady); ++failures; }
        const int32_t alt = interpolate(20000, -20000, 20000, -20000, t);
        if (std::abs(alt) > 12000) { std::printf("phase %u alternation %d\n", t, alt); ++failures; }
    }
    if (interpolate(32767, 32767, 32767, 32767, 0x800u) > 32767) ++failures;
    std::puts(failures ? "native sound mixer: FAILED" : "native sound mixer: ok");
    return failures ? 1 : 0;
}
