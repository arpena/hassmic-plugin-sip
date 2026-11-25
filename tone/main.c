/* The tone example: a music source, the smallest that plays.  It plays a steady sine on the Echo's speaker the way
 * a music player plays music (a Lyrion or Snapcast client would decode its stream where this computes a sine), and
 * shows the parts of audio.music once (sdk/PROTOCOL.md "Music"):
 *
 *   - hm_music_open() gives a stream (a socket) that plays what is written into it, at the rate and channels asked
 *     for.  Opening one makes the plugin the newest music source: Sendspin and a phone over Bluetooth are paused.
 *   - music_writable is called while the stream takes more and the player says it plays (hm_music_state): it writes
 *     until the socket is full (EAGAIN).  The socket's buffer is the back-pressure: hassmic reads it at playback
 *     speed, so the tone is computed as fast as it plays and no faster.
 *   - music_cmd is what the Echo asks of a player: pause (the action button, "<wake word>, stop", another source
 *     that started, Home Assistant's media player), resume (the button again, Home Assistant), stop (Home
 *     Assistant), next and previous (the button pressed twice or three times).  The player does it and says what it
 *     does now with hm_music_state: the button, the stop word and Home Assistant go by what it said last.
 *   - music_pos says where the stream is, five times a second: frames that reached the speaker, and when.  A player
 *     in a group keeps in step by it; this one only logs it now and then.
 *
 * hassmic does the rest: the duck under the voice assistant, the volume, the light ring's music effects.  The
 * template's own code: 0BSD (LICENSE), yours to relicense.  sdk/ is hassmic's, MIT (sdk/LICENSE). */
#include "hassmic_plugin.h"
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* 48 kHz stereo: what music is.  hassmic takes 8 to 48 kHz, 1 or 2 channels, and plays it as it is */
#define RATE 48000
#define CHANNELS 2

static int freq = 440, level_db = -20, stream = -1, state = HM_MUSIC_STOPPED, opened;
static double phase, amp;
static unsigned long long last_logged;

static void say(int st)
{
    state = st;
    hm_music_state(st, "A tone", "hassmic's plugin template");
    hm_log("%s", st == HM_MUSIC_PLAYING ? "playing" : st == HM_MUSIC_PAUSED ? "paused" : "stopped");
}

static void on_config(void *ctx, const struct hm_setting *s, int n)
{
    (void)ctx;
    for (int i = 0; i < n; i++) {
        if (!strcmp(s[i].name, "frequency")) freq = atoi(s[i].value);
        else if (!strcmp(s[i].name, "level")) level_db = atoi(s[i].value);
    }
    if (freq < 50 || freq > 4000) freq = 440;
    if (level_db < -60 || level_db > -6) level_db = -20;
    amp = 32767.0 * pow(10.0, level_db / 20.0);
    hm_log("a tone of %d Hz at %d dB", freq, level_db);
}

/* As much as the stream takes, 10 ms at a time.  A write that takes part of a block is kept to the frame: the rest
 * of the block is computed again next time (a sine needs no buffer of its own) */
static void on_writable(void *ctx, int fd)
{
    static int16_t b[RATE / 100 * CHANNELS];
    (void)ctx;
    for (;;) {
        double p = phase, step = 2 * M_PI * freq / RATE; ssize_t w;
        for (int i = 0; i < RATE / 100; i++) { int16_t v = (int16_t)(amp * sin(p)); b[2 * i] = b[2 * i + 1] = v; p += step; }
        w = write(fd, b, sizeof b);
        if (w <= 0) {
            if (w < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                /* the stream is gone: another plugin took the speaker, or hassmic ended it */
                hm_log("the stream ended (%s)", strerror(errno));
                hm_music_close(); stream = -1; say(HM_MUSIC_STOPPED);
            }
            return;
        }
        phase = fmod(phase + step * (double)(w / (2 * CHANNELS)), 2 * M_PI);
        if ((size_t)w < sizeof b) return;
    }
}

static void open_stream(void)
{
    int why = 0;
    stream = hm_music_open(RATE, CHANNELS, &why);
    if (stream < 0) { hm_log("no stream (error %d)", why); return; }
    say(HM_MUSIC_PLAYING);
}

static void on_cmd(void *ctx, int cmd, int why)
{
    static const char *const whys[] = { "?", "the action button", "the stop word", "another source", "Home Assistant" };
    (void)ctx;
    hm_log("asked to %s by %s", cmd == HM_MCMD_PAUSE ? "pause" : cmd == HM_MCMD_RESUME ? "resume" : cmd == HM_MCMD_STOP ? "stop" : "skip", whys[why >= 1 && why <= 4 ? why : 0]);
    switch (cmd) {
    case HM_MCMD_PAUSE: if (state == HM_MUSIC_PLAYING) say(HM_MUSIC_PAUSED); break;
    case HM_MCMD_RESUME: if (stream < 0) open_stream(); else say(HM_MUSIC_PLAYING); break;
    case HM_MCMD_STOP: if (stream >= 0) { hm_music_close(); stream = -1; } say(HM_MUSIC_STOPPED); break;
    default: break;                     /* a tone has no next one */
    }
}

static void on_pos(void *ctx, uint64_t frames, uint64_t at_us, uint32_t queued_us)
{
    (void)ctx; (void)at_us;
    if (frames / RATE >= last_logged + 60) { last_logged = frames / RATE; hm_log("%llu s played (%u ms queued)", (unsigned long long)(frames / RATE), queued_us / 1000); }
}

/* the first tick: the stream opens (not in ready: hassmic takes a stream only from a plugin that runs, after READY) */
static void on_tick(void *ctx) { (void)ctx; if (!opened) { opened = 1; open_stream(); } }

static void on_stop(void *ctx) { (void)ctx; if (stream >= 0) { hm_music_close(); stream = -1; } }

int main(void)
{
    static const struct hm_callbacks cb = { .config = on_config, .tick = on_tick, .stop = on_stop, .music_writable = on_writable,
                                            .music_cmd = on_cmd, .music_pos = on_pos };
    return hm_run(&cb, NULL) ? 1 : 0;
}
