// An ordinary output validator: judge input answer feedback < team_output.
// Exit 42 accepted, 43 wrong answer.  Recomputes from the input, so no
// answer file is needed, and works unchanged in a conventional package.
#include <cstdio>
#include <cstdint>
#include <string>

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: judge input answer feedback\n"); return 1; }
    FILE *in = fopen(argv[1], "r");
    if (!in) { fprintf(stderr, "cannot open %s\n", argv[1]); return 1; }
    long long n = 0;
    if (fscanf(in, "%lld", &n) != 1) { fclose(in); return 1; }
    unsigned long long sum = 0, evens = 0, v;
    for (long long i = 0; i < n; ++i) {
        if (fscanf(in, "%llu", &v) != 1) { fclose(in); return 1; }
        sum += v; evens += (v % 2 == 0);
    }
    fclose(in);

    unsigned long long gs = 0, ge = 0;
    bool ok = (scanf("%llu %llu", &gs, &ge) == 2) && gs == sum && ge == evens;
    if (!ok) {
        std::string p = std::string(argv[3]) + "/judgemessage.txt";
        if (FILE *f = fopen(p.c_str(), "w")) {
            fprintf(f, "expected sum=%llu evens=%llu, got sum=%llu evens=%llu\n",
                    sum, evens, gs, ge);
            fclose(f);
        }
        return 43;
    }
    return 42;
}
