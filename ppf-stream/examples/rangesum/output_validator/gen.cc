// gen N SEED  ->  test case input on stdout.
//
// Deterministic by construction: splitmix64 is written out here rather than
// taken from a library, all arithmetic is exact 64-bit integer, and nothing
// reads the clock, the locale, or the environment.  The same N and SEED give
// the same bytes on any platform and any compiler.
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>

static uint64_t splitmix64(uint64_t &x) {
    uint64_t z = (x += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

// Uniform in [0, n) by rejection on the smallest covering power of two.
static uint64_t below(uint64_t &s, uint64_t n) {
    if (n <= 1) return 0;
    int bits = 64; uint64_t m = n - 1;
    while (bits > 1 && !(m >> (bits - 1))) --bits;
    uint64_t mask = (bits == 64) ? ~0ULL : ((1ULL << bits) - 1);
    for (;;) { uint64_t v = splitmix64(s) & mask; if (v < n) return v; }
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: gen N SEED\n"); return 1; }
    long long n = atoll(argv[1]);
    uint64_t s = strtoull(argv[2], nullptr, 10);
    // Domain-separate so different problems with the same seed diverge.
    s ^= 0x726E6773756D0001ULL;
    printf("%lld\n", n);
    for (long long i = 0; i < n; ++i)
        printf("%llu\n", (unsigned long long)below(s, 1000000000ULL));
    return 0;
}
