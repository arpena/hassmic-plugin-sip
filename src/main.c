/* The example plugin: a placeholder that shows each common piece of a hassmic plugin once.  It is not useful, on
 * purpose: replace what it does, keep what you need of how it does it.
 *
 * What it does: it counts how often it has started (kept in its storage across restarts), and while the owner has
 * entered a server on its card, it connects there and sends a greeting now and then ("hello from <node> (start 3,
 * greeting 12)"), as one UDP datagram or one line on a TCP connection.  The action button held a second greets at once
 * (capability `button`).
 *
 * How a plugin lives (sdk/hassmic_plugin.h, sdk/PROTOCOL.md): main() hands its callbacks to hm_run(), and everything
 * happens inside them, on hm_run()'s one thread.  The order is always: config (every setting), ready, then tick
 * about once a second and config again whenever the owner changes a setting, then stop.  There is no loop of the
 * plugin's own: a callback that blocks holds up the client, which answers hassmic's PINGs between callbacks, and
 * hassmic ends a plugin that leaves them unanswered for 30 s.
 *
 * The sandbox (README.md "What a plugin is"): no files, no sockets of its own making, stderr is hassmic's log.  What
 * it keeps, hassmic keeps for it (storage); what it reaches, hassmic connects for it (hm_connect, to the server the
 * owner entered and nothing else).
 *
 * The template's own code: 0BSD (LICENSE), yours to relicense.  sdk/ is hassmic's, MIT (sdk/LICENSE). */
#include "hassmic_plugin.h"
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

/* Storage keys: [a-z0-9._-], 1 to 48 bytes.  All values together must fit the kB the manifest asks for
 * ("storage": 1, i.e. 1 kB): two short numbers here */
#define KEY_STARTS "starts"
#define KEY_SENT   "sent"
/* How often the greeting count is written while the plugin runs.  The Echo's flash takes ten writes, then one a
 * minute (per plugin); a plugin that writes every second is refused (HM_ERR_BUSY) and wears the flash for nothing.
 * Write on a change of state, at stop, and at most every few minutes for what a crash would otherwise lose */
#define SAVE_MS (10 * 60 * 1000)
/* Waits between failed connects.  hassmic refuses a plugin's 31st connect in a minute (HM_ERR_BUSY), and every
 * failure is a line in its log: back off, double the wait, start low again once it worked */
#define RETRY_MIN_MS 5000
#define RETRY_MAX_MS (5 * 60 * 1000)

/* The protocol select's options in the manifest's order, which is also the order of the net.server entries: entry
 * 0 is the UDP one, entry 1 the TCP one.  hm_connect() names an entry by its place */
enum { P_UDP, P_TCP };
static const char *const PROTOS[] = { "UDP", "TCP" };

/* The settings as the plugin uses them.  The defaults here are the manifest's: CONFIG carries every row, with the
 * owner's value or the manifest's default, but a plugin that falls back sensibly never depends on that */
static struct cfg {
    char greeting[65];
    int interval;                       /* seconds */
    int proto;
    int verbose;
    char server[256];                   /* only to know whether one is entered: hassmic resolves and connects */
    int port;                           /* 0: not entered */
} cfg = { "hello", 60, P_UDP, 0, "", 0 };

static long starts, sent, sent_saved;
static long long saved_at, next_greeting, retry_at, backoff;
static int sock = -1, sock_proto, last_why = -1, unset;

static long long mono_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

/* ---- logging.  hm_log() is printf into hassmic's log, shown as "plugin <id>: ...".  hassmic keeps 50 lines a
 * minute of each plugin and counts the rest ("(12 more lines in that minute, not kept)"): the Echo's log is on
 * flash and is what its owner reads when something is wrong.  So log what changed (started, connected, lost the
 * server, a refusal), once, and not what happens every time; the "verbose" switch below is how not to do it, behind
 * a switch that is off.  Never log what the owner would not want in a log they may post in an issue: a secret
 * setting's value, what was said to the Echo. */

static const char *why_text(int why)
{
    switch (why) {
    case HM_ERR_UNSET: return "no server or no port entered";
    case HM_ERR_NOT_FOUND: return "its name has no address";
    case HM_ERR_FAILED: return "it did not answer";
    case HM_ERR_DENIED: return "hassmic does not let plugins reach that address";
    case HM_ERR_BUSY: return "too many connects in the last minute";
    default: return "refused";
    }
}

/* ---- storage */

/* A number kept under KEY: 0 when there is none yet (a first start, or the owner removed and installed it again:
 * storage goes with the plugin) */
static long load_number(const char *key)
{
    char b[24];
    size_t n = 0;
    int r = hm_store_get(key, b, sizeof b - 1, &n);
    if (r == 1) return 0;                               /* no such key */
    if (r < 0 || n >= sizeof b) { hm_log("could not read \"%s\" from storage: starting from 0", key); return 0; }
    b[n] = 0;
    return strtol(b, NULL, 10);
}

/* 1 when the value is written or will be: hm_store_put's 0 (written) and 1 (the Echo takes no write now; the client
 * keeps the latest value of the key and writes it within a minute or so).  -1 is "neither": HM_ERR_BUSY when the
 * client has no room to keep it either, HM_ERR_LIMIT over the kB the manifest asked for.  A plugin decides what a
 * lost write costs it; here a count that is a little behind after a crash, so it says so and goes on */
static int save_number(const char *key, long v)
{
    char b[24];
    int why = 0, r = hm_store_put(key, b, (size_t)snprintf(b, sizeof b, "%ld", v), &why);
    if (r >= 0) return 1;
    if (why == HM_ERR_BUSY) hm_log("\"%s\" not stored: the Echo took too many writes of this plugin's lately", key);
    else hm_log("\"%s\" not stored (error %d)", key, why);
    return 0;
}

static void save_sent(void)
{
    if (sent == sent_saved) return;
    if (save_number(KEY_SENT, sent)) sent_saved = sent;
    saved_at = mono_ms();               /* also after a failure: try again in SAVE_MS, not at every tick */
}

/* ---- the server */

static void close_sock(const char *why)
{
    if (sock < 0) return;
    close(sock);
    sock = -1;
    if (why) {
        hm_log("lost the server: %s", why);
        backoff = RETRY_MIN_MS;
        retry_at = mono_ms() + backoff;
    }
}

/* hm_connect asks hassmic for a socket to the server the owner entered, for one net.server entry of the manifest.
 * The plugin cannot pick another address (it may read the one it got, getpeername(), as any connected socket's; it
 * has the owner's host row as text too): hassmic resolves the owner's host, refuses loopback,
 * the Echo itself and the like, connects (up to 10 s; the client answers PINGs meanwhile) and hands over the
 * descriptor: connected (UDP too: send() reaches that server only), non-blocking, the plugin's to close.  Only from
 * tick or a callback after ready: in ready itself hassmic refuses it (HM_ERR_BAD), since READY is due 5 s after the
 * start and a connect may take 10 */
static void open_sock(void)
{
    int why = 0, fd = hm_connect(cfg.proto, 0, &why);   /* port 0: the one the owner entered in the entry's port row */
    long long now = mono_ms();
    if (fd < 0) {
        if (why != last_why) hm_log("cannot reach the server: %s", why_text(why));     /* once per reason */
        last_why = why;
        if (why == HM_ERR_UNSET) { unset = 1; return; } /* nothing to do until the owner enters one (a new CONFIG) */
        backoff = backoff ? (backoff * 2 > RETRY_MAX_MS ? RETRY_MAX_MS : backoff * 2) : RETRY_MIN_MS;
        retry_at = now + backoff;
        return;
    }
    sock = fd;
    sock_proto = cfg.proto;
    backoff = 0;
    last_why = -1;
    next_greeting = now;                /* the first greeting at once */
    hm_log("connected to the server (%s)", PROTOS[sock_proto]);
}

/* TCP: the server closed the connection (a FIN, or an error).  A server that says something is read and ignored */
static int peer_gone(void)
{
    struct pollfd p = { sock, POLLIN, 0 };
    char b[256];
    if (poll(&p, 1, 0) <= 0) return 0;
    if (p.revents & (POLLERR | POLLHUP)) return 1;
    for (;;) {
        long k = recv(sock, b, sizeof b, MSG_DONTWAIT);
        if (k > 0) continue;
        return k == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR);
    }
}

static void greet(void)
{
    char msg[200];
    int n = snprintf(msg, sizeof msg, "%s from %s (start %ld, greeting %ld)%s", cfg.greeting, hm_info()->node, starts,
                     sent + 1, sock_proto == P_TCP ? "\n" : "");
    long k;
    if (n < 0 || n >= (int)sizeof msg) return;
    /* MSG_DONTWAIT: the socket is non-blocking anyway (and F_SETFL is not allowed to change that); MSG_NOSIGNAL: a
     * closed TCP connection answers EPIPE instead of killing the plugin with SIGPIPE */
    k = send(sock, msg, (size_t)n, MSG_DONTWAIT | MSG_NOSIGNAL);
    if (k == n) {
        sent++;
        if (cfg.verbose) hm_log("greeted the server: %.*s", sock_proto == P_TCP ? n - 1 : n, msg);
        return;
    }
    if (k < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return;  /* full: skip this one */
    /* UDP: ECONNREFUSED is the answer to an earlier datagram that nobody took (nothing listens there now); UDP goes on */
    if (k < 0 && errno == ECONNREFUSED && sock_proto == P_UDP) return;
    /* TCP cut short or failed: the line would arrive in pieces.  Connect anew */
    close_sock(k < 0 ? strerror(errno) : "only part of a greeting was written");
}

/* ---- the callbacks */

static int pick(const char *v, const char *const *opts, int n, int dflt)
{
    for (int i = 0; i < n; i++) if (!strcmp(v, opts[i])) return i;
    return dflt;
}

/* Every setting, as text: before ready, and again whenever the owner changes one.  A bool is "0" or "1", an int its
 * decimal, a select the option's text, a host or text row what was typed ("" when nothing was).  hassmic keeps each
 * value within the manifest's rules; parse it defensively all the same.
 *
 * When the owner changes a row that a net.server entry names (here server and port), hassmic stops the plugin and
 * starts it again: a socket it holds is never to a server the owner no longer means.  Any other change arrives
 * here while it runs, and the plugin follows it itself: the protocol, for one */
static void on_config(void *ctx, const struct hm_setting *s, int n)
{
    struct cfg c = cfg;
    (void)ctx;
    for (int i = 0; i < n; i++) {
        const char *k = s[i].name, *v = s[i].value;
        if (!strcmp(k, "greeting")) snprintf(c.greeting, sizeof c.greeting, "%s", *v ? v : "hello");
        else if (!strcmp(k, "interval")) { long x = strtol(v, NULL, 10); c.interval = x < 1 ? 60 : x > 3600 ? 3600 : (int)x; }
        else if (!strcmp(k, "protocol")) c.proto = pick(v, PROTOS, 2, P_UDP);
        else if (!strcmp(k, "verbose")) c.verbose = !strcmp(v, "1");
        else if (!strcmp(k, "server")) snprintf(c.server, sizeof c.server, "%s", v);
        else if (!strcmp(k, "port")) c.port = atoi(v);
    }
    if (sock >= 0 && c.proto != sock_proto) { close_sock(NULL); retry_at = 0; backoff = 0; }   /* the other entry */
    if (c.interval != cfg.interval) next_greeting = 0;
    unset = 0;                          /* a new CONFIG: worth asking again */
    cfg = c;
}

/* Settings are in; the plugin runs once this returns (the client sends READY then).  Read what storage kept, and
 * prepare.  Do not connect here (see open_sock); do nothing that takes long: READY is due 5 s after the start */
static void on_ready(void *ctx)
{
    (void)ctx;
    starts = load_number(KEY_STARTS) + 1;
    sent = sent_saved = load_number(KEY_SENT);
    save_number(KEY_STARTS, starts);    /* one write a start: well inside the Echo's ten */
    saved_at = mono_ms();
    hm_log("started %ld time%s, %ld greeting%s sent before; %s", starts, starts == 1 ? "" : "s", sent, sent == 1 ? "" : "s",
           cfg.server[0] ? "connects at its first tick" : "no server entered yet: nothing is sent");
}

/* About once a second from READY on: whatever a plugin does by the clock.  Keep it short: hassmic's messages are
 * read between two ticks */
static void on_tick(void *ctx)
{
    long long now = mono_ms();
    (void)ctx;
    if (sock >= 0 && sock_proto == P_TCP && peer_gone()) close_sock("it closed the connection");
    if (sock < 0 && cfg.server[0] && !unset && now >= retry_at) open_sock();
    if (sock >= 0 && now >= next_greeting) {
        greet();
        next_greeting = now + cfg.interval * 1000LL;
    }
    if (now - saved_at >= SAVE_MS) save_sent();
}

/* The Echo's buttons (`button`): every press and hold that Home Assistant's "Button" event entity hears, and
 * nothing else (never the press that let a browser in or accepted an install).  The plugin only hears: the button
 * does on the Echo what it does anyway, here also whatever the hold means to the assistant.  The action button held
 * a second (HM_BUTTON_HOLD; a press is HM_BUTTON_PRESS, let go within a second): the next tick greets */
static void on_button(void *ctx, int key, int how)
{
    (void)ctx;
    if (key != HM_BUTTON_ACTION || how != HM_BUTTON_HOLD) return;
    hm_log("the action button held: greeting %s", sock >= 0 ? "now" : "once connected");
    next_greeting = 0;
}

/* hassmic says to end: the owner switched the plugin off, changed its server, removed or updated it, or hassmic
 * itself stops.  2 s, then it is killed.  Storage writes still go through here, at once or not at all (the client
 * keeps nothing for later from now on): save what a restart should find.  A crash or a kill never gets here, which
 * is why on_tick saves now and then too */
static void on_stop(void *ctx)
{
    (void)ctx;
    sent_saved = -1;                    /* write it even if unchanged since the last save: cheap, one write */
    save_sent();
    close_sock(NULL);
}

/* ---- Reading the log: not this example's, left here as a sketch.
 *
 * A plugin that forwards or watches hassmic's log asks for "log.read": true in the manifest's capabilities (the owner
 * reads "Reads this Echo's technical log" at install), sets .log in its callbacks, and starts following from ready:
 *
 *     static void on_log(void *ctx, const char *cursor, int gap, const char *const *lines, int n)
 *     {
 *         for (int i = 0; i < n; i++) {
 *             if (strstr(lines[i], "plugin <your id>: ")) continue;    // its own lines come back: never feed them on
 *             ... lines[i]: its time in front ("2026-11-21 10:00:00.123Z ..." or "boot+12.345 ..."), no line end ...
 *         }
 *         remember cursor, and hm_store_put it now and then and at stop: a restart goes on from there
 *     }
 *     in on_ready:  hm_log_follow(the kept cursor, or "start" for the oldest line kept);
 *
 * gap 1: lines between the cursor asked for and these are gone (the log rotated).  The next LOG comes once on_log
 * returned (flow control): a slow callback holds the log back on the Echo, never loses it.
 *
 * What a plugin gets is less than the settings page shows, by hassmic's rule and not the plugin's: never a line
 * hassmic marks private (what was said to the Echo and what it answered, names of networks, Bluetooth devices,
 * timers and servers, media addresses, Home Assistant's error texts) or sealed (a wake word plugin's own lines); of
 * the system's own lines only its events; MAC and Bluetooth addresses and the Sendspin pairing token blanked.  The
 * whole log (log.read.full) is for official plugins only, and a wake word plugin may not read the log at all.
 * sdk/PROTOCOL.md "The log" has the rest. */

int main(void)
{
    /* Unused callbacks stay NULL: files, file_check, the wake word ones, log (each needs its capability) */
    static const struct hm_callbacks cb = { .config = on_config, .ready = on_ready, .tick = on_tick, .stop = on_stop, .button = on_button };
    /* 0 when hassmic said to stop; -1 when it refused the plugin or went away.  A plugin that exits by itself is
     * started again after 1, 5 and 30 s, then every 5 minutes, and five ends in ten minutes switch it off */
    return hm_run(&cb, NULL) ? 1 : 0;
}
