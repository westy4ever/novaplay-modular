/* Byse / n1mwq captcha proof-of-work -- native solver.
 *
 * Same algorithm as extractors/hosts.py _n1mwq_pow_hash (port of the site's
 * pow-DEJGtdh2.js "gr()"): ChaCha-style quarter rounds seeded with SHA-256 IV
 * words, a 512-word scratch block mixed twice, 8 output words. The box has no
 * numpy and plain Python manages only a few hundred hashes/s, so NovaPlay runs
 * this instead (built static for ARMv7 hard-float: tools/build_byse_pow.sh).
 *
 *   byse_pow <nonce> <difficulty> [timeout_ms]   -> prints the solution
 *   byse_pow --hash <text>                       -> prints the 8 words (tests)
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define BLOCK 512
#define MASK (BLOCK - 1)
#define MUL1 2654435761u
#define MUL2 2246822519u

static inline uint32_t rotl(uint32_t x, int n) { return (x << n) | (x >> (32 - n)); }

static inline void mix(uint32_t *e) {
    e[0] += e[1]; e[3] = rotl(e[3] ^ e[0], 16);
    e[2] += e[3]; e[1] = rotl(e[1] ^ e[2], 12);
    e[0] += e[1]; e[3] = rotl(e[3] ^ e[0], 8);
    e[2] += e[3]; e[1] = rotl(e[1] ^ e[2], 7);
}

static void absorb(uint32_t *e, const unsigned char *d, size_t n) {
    for (size_t i = 0; i < n; i++) {
        e[0] = rotl(e[0] + d[i], 7);
        mix(e);
    }
}

/* finish the hash from a state that has absorbed all input bytes */
static void finish(uint32_t *e, uint32_t out[8]) {
    static uint32_t r[BLOCK];
    for (int i = 0; i < 8; i++) mix(e);
    for (int i = 0; i < BLOCK; i++) { mix(e); r[i] = e[0] ^ e[2]; }
    for (int round = 0; round < 2; round++) {
        for (int s = 0; s < BLOCK; s++) {
            uint32_t a = r[s] & MASK;
            uint32_t c = rotl(r[s] + r[a], 13);
            c ^= r[(s + 1) & MASK] * MUL1;
            r[s] = c;
            e[0] ^= c;
            mix(e);
        }
    }
    int chunk = BLOCK / 8;
    for (int i = 0; i < 8; i++) {
        mix(e);
        uint32_t s = e[0];
        for (int c = 0; c < chunk; c++) {
            uint32_t d = r[i * chunk + c];
            s = rotl(s + d, 5);
            s ^= d * MUL2;
        }
        out[i] = s ^ e[2];
    }
}

static int lzb(const uint32_t w[8]) {
    int t = 0;
    for (int i = 0; i < 8; i++) {
        if (w[i] == 0) { t += 32; continue; }
        return t + __builtin_clz(w[i]);
    }
    return t;
}

static const uint32_t IV[4] = {1779033703u, 3144134277u, 1013904242u, 2773480762u};

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

int main(int argc, char **argv) {
    if (argc >= 3 && strcmp(argv[1], "--hash") == 0) {
        uint32_t e[4], out[8];
        memcpy(e, IV, sizeof e);
        absorb(e, (const unsigned char *)argv[2], strlen(argv[2]));
        finish(e, out);
        for (int i = 0; i < 8; i++) printf("%u%c", out[i], i == 7 ? '\n' : ' ');
        printf("%d\n", lzb(out));
        return 0;
    }
    if (argc < 3) {
        fprintf(stderr, "usage: %s <nonce> <difficulty> [timeout_ms]\n", argv[0]);
        return 2;
    }
    const char *nonce = argv[1];
    int difficulty = atoi(argv[2]);
    double timeout = argc > 3 ? atof(argv[3]) : 20000.0;
    if (difficulty <= 0) { puts("0"); return 0; }

    uint32_t base[4];
    memcpy(base, IV, sizeof base);
    absorb(base, (const unsigned char *)nonce, strlen(nonce));
    absorb(base, (const unsigned char *)":", 1);

    double t0 = now_ms();
    char num[24];
    for (unsigned long long s = 0;; s++) {
        int n = snprintf(num, sizeof num, "%llu", s);
        uint32_t e[4], out[8];
        memcpy(e, base, sizeof e);
        absorb(e, (const unsigned char *)num, (size_t)n);
        finish(e, out);
        if (lzb(out) >= difficulty) {
            printf("%llu\n", s);
            return 0;
        }
        if ((s & 255) == 255 && now_ms() - t0 > timeout) {
            fprintf(stderr, "timeout after %llu tries\n", s + 1);
            return 1;
        }
    }
}
