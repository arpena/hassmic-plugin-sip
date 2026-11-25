/* G.711 A-law and mu-law, and the 2:1 rate change around them (codec.c).  hassmic hands a call's audio over as
 * 16 kHz mono PCM; a phone speaks G.711 at 8 kHz. */
#ifndef SIP_CODEC_H
#define SIP_CODEC_H
#include <stddef.h>
#include <stdint.h>

uint8_t g711_alaw(int16_t s);
uint8_t g711_ulaw(int16_t s);
int16_t g711_alaw_dec(uint8_t a);
int16_t g711_ulaw_dec(uint8_t u);

struct half { float x[64]; unsigned n; };       /* a FIR's history (63 taps) */
void half_down(struct half *h, const int16_t *in, size_t n, int16_t *out);    /* n at 16 kHz -> n/2 at 8 kHz */
void half_up(struct half *h, const int16_t *in, size_t n, int16_t *out);      /* n at 8 kHz -> 2n at 16 kHz */

void md5(const void *data, size_t len, uint8_t out[16]);    /* RFC 1321: SIP's digest authentication (RFC 2617) */
#endif
