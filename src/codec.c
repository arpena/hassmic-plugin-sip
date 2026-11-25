/* G.711 as ITU-T G.711 and Sun's g711.c have it, a 63-tap Kaiser FIR for 16 kHz <-> 8 kHz, and MD5.  From hassmic's
 * call.c and hash.c of PR #186, where tests/unit/call_test.c and hash_test.c checked them: every 16-bit value through
 * each law and back within its step, 300-3400 Hz within 0.1 dB through 8 kHz and back, a 5 kHz tone's alias -70 dB,
 * RFC 1321's and RFC 2617's vectors.  tests/unit.c here checks the same. */
#include "codec.h"
#include <math.h>
#include <string.h>

uint8_t g711_alaw(int16_t pcm)
{
    static const int end[8] = { 0x1f, 0x3f, 0x7f, 0xff, 0x1ff, 0x3ff, 0x7ff, 0xfff };
    int v = pcm >> 3, mask = 0xd5, seg = 0;                 /* 13 bits */
    if (v < 0) { v = -v - 1; mask = 0x55; }
    if (v > 0xfff) v = 0xfff;
    while (v > end[seg]) seg++;
    return (uint8_t)((seg << 4 | ((seg < 2 ? v >> 1 : v >> seg) & 0x0f)) ^ mask);
}

int16_t g711_alaw_dec(uint8_t a)
{
    int t, seg;
    a ^= 0x55;
    t = (a & 0x0f) << 4; seg = (a & 0x70) >> 4;
    if (seg == 0) t += 8; else if (seg == 1) t += 0x108; else { t += 0x108; t <<= seg - 1; }
    return (int16_t)(a & 0x80 ? t : -t);
}

uint8_t g711_ulaw(int16_t pcm)
{
    static const int end[8] = { 0x3f, 0x7f, 0xff, 0x1ff, 0x3ff, 0x7ff, 0xfff, 0x1fff };
    int v = pcm >> 2, mask = 0xff, seg = 0;                 /* 14 bits */
    if (v < 0) { v = -v; mask = 0x7f; }
    if (v > 8159) v = 8159;
    v += 0x21;
    while (seg < 8 && v > end[seg]) seg++;
    if (seg == 8) return (uint8_t)(0x7f ^ mask);
    return (uint8_t)((seg << 4 | ((v >> (seg + 1)) & 0x0f)) ^ mask);
}

int16_t g711_ulaw_dec(uint8_t u)
{
    int t;
    u = (uint8_t)~u;
    t = ((u & 0x0f) << 3) + 0x84; t <<= (u & 0x70) >> 4;
    return (int16_t)(u & 0x80 ? 0x84 - t : t - 0x84);
}

/* ---- 16 kHz <-> 8 kHz: a windowed sinc (Kaiser, beta 6: -63 dB), cut at 3.85 kHz of 16, unity gain */

#define TAPS 63
static float fir[TAPS];
static int fir_made;

static double bessel_i0(double x)
{
    double sum = 1, t = 1;
    for (int k = 1; k < 30; k++) { t *= x / (2 * k); sum += t * t; }
    return sum;
}

static void fir_make(void)              /* one thread: the plugin's (sdk/hassmic_plugin.h) */
{
    double sum = 0, wc = 2 * M_PI * 3850.0 / 16000.0, beta = 6;
    if (fir_made) return;
    for (int i = 0; i < TAPS; i++) {
        double k = i - (TAPS - 1) / 2.0, s = k == 0 ? wc / M_PI : sin(wc * k) / (M_PI * k), r = 2.0 * i / (TAPS - 1) - 1;
        fir[i] = (float)(s * bessel_i0(beta * sqrt(1 - r * r)) / bessel_i0(beta)); sum += fir[i];
    }
    for (int i = 0; i < TAPS; i++) fir[i] = (float)(fir[i] / sum);
    fir_made = 1;
}

static float fir_push(struct half *h, float v)          /* one sample in at 16 kHz, the filter's output */
{
    float acc = 0;
    memmove(h->x, h->x + 1, (TAPS - 1) * sizeof h->x[0]); h->x[TAPS - 1] = v;
    for (int i = 0; i < TAPS; i++) acc += fir[i] * h->x[i];
    return acc;
}

static int16_t clip(float v) { return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : lrintf(v)); }

void half_down(struct half *h, const int16_t *in, size_t n, int16_t *out)
{
    fir_make();
    for (size_t i = 0; i < n; i++) { float y = fir_push(h, in[i]); if (h->n++ & 1) out[i / 2] = clip(y); }
}

void half_up(struct half *h, const int16_t *in, size_t n, int16_t *out)
{
    fir_make();
    for (size_t i = 0; i < n; i++) { out[2 * i] = clip(2 * fir_push(h, in[i])); out[2 * i + 1] = clip(2 * fir_push(h, 0)); }
}

/* ---- MD5 (RFC 1321) */

#define ROL(x, n) (((x) << (n)) | ((x) >> (32 - (n))))

void md5(const void *data, size_t len, uint8_t out[16])
{
    static const uint32_t K[64] = {
        0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501, 0x698098d8, 0x8b44f7af,
        0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821, 0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa,
        0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8, 0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8,
        0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
        0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665, 0xf4292244, 0x432aff97,
        0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1, 0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
        0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391 };
    static const uint8_t R[64] = { 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
        5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21 };
    uint32_t h[4] = { 0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476 };
    const uint8_t *p = data; uint8_t tail[128] = { 0 }; size_t full = len / 64 * 64, rest = len - full, tl;
    memcpy(tail, p + full, rest); tail[rest] = 0x80;
    tl = rest < 56 ? 64 : 128;
    for (int i = 0; i < 8; i++) tail[tl - 8 + i] = (uint8_t)(((uint64_t)len * 8) >> (8 * i));    /* little endian */
    for (size_t off = 0; off < full + tl; off += 64) {
        const uint8_t *b = off < full ? p + off : tail + (off - full); uint32_t w[16], a = h[0], bb = h[1], c = h[2], d = h[3];
        for (int i = 0; i < 16; i++) w[i] = b[4 * i] | b[4 * i + 1] << 8 | b[4 * i + 2] << 16 | (uint32_t)b[4 * i + 3] << 24;
        for (int i = 0; i < 64; i++) {
            uint32_t f; int g;
            if (i < 16) { f = (bb & c) | (~bb & d); g = i; } else if (i < 32) { f = (d & bb) | (~d & c); g = (5 * i + 1) % 16; }
            else if (i < 48) { f = bb ^ c ^ d; g = (3 * i + 5) % 16; } else { f = c ^ (bb | ~d); g = 7 * i % 16; }
            uint32_t t = d; d = c; c = bb; bb = bb + ROL(a + f + K[i] + w[g], R[i]); a = t;
        }
        h[0] += a; h[1] += bb; h[2] += c; h[3] += d;
    }
    for (int i = 0; i < 4; i++) { out[4 * i] = (uint8_t)h[i]; out[4 * i + 1] = (uint8_t)(h[i] >> 8); out[4 * i + 2] = (uint8_t)(h[i] >> 16); out[4 * i + 3] = (uint8_t)(h[i] >> 24); }
}
