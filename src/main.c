/* hassmic-plugin-sip: the Echo as one extension of the home's phone system (Asterisk, FreePBX, a Fritz!Box), so a
 * doorbell, a desk phone or a softphone can ring it and Home Assistant can have it call them.  A small SIP user agent
 * (RFC 3261 over UDP) for one account on one server in the home's network, with RTP (RFC 3550) and G.711: no TLS, no
 * SRTP, no NAT traversal, no DTMF, no transfer or hold.  Written for hassmic as its PR #186 (`sip.c`) and moved into a
 * plugin at the maintainer's request: the call itself (the Echo's ring, its microphone through hassmic's call
 * processing, its speaker, one call at a time beside Drop In) is hassmic's `call` capability; SIP, RTP and the codec
 * are here.
 *
 *   register   REGISTER to the server, the user's password by digest (RFC 2617, MD5, qop=auth or none), again at 80 %
 *              of what the server granted; after a refusal (a wrong password) only every REFUSED_MS, so a PBX's
 *              fail2ban never sees a flood; a new address of the Echo registers at once
 *   incoming   an INVITE (only the server reaches the plugin's SIP port: its peer is fixed to it) with an SDP offer:
 *              hm_call_incoming, and while hassmic rings 100, 180; a person at the Echo answers (call_answer): 200 with
 *              our answer, the call live.  Refused: busy (486), do not disturb, the microphones off or too many rings
 *              (480), no codec in common (488); declined at the Echo (603), not answered (480)
 *   outgoing   Home Assistant's or the card's "call" (an action that brings a call token): hm_call_out, INVITE
 *              sip:<number>@<server> with our offer, through any 401/407 once with the password; 180: rings there;
 *              2xx: ACK and live; 3xx-6xx: ACK and a result; hung up before an answer: CANCEL (after the first
 *              provisional, RFC 3261 9.1)
 *   in dialog  BYE either way, a re-INVITE answered with the same SDP, OPTIONS (a PBX's qualify) 200 anywhere
 * Every request goes to the server (an outbound proxy, as phones on a PBX are set up), the Record-Route set kept as
 * Route.  Retransmissions as RFC 3261 17 has them for UDP: T1 500 ms doubling to T2 4 s, given up after 64 T1.  A call
 * that ended here still finishes its transactions (the ACK of a 487, a CANCEL, a late 200: ACK and BYE) in `z`.
 *
 * Media: RTP on the plugin's second port, its peer fixed to the address the SDP named (which only hassmic's rules
 * allow: a private one), no RTCP.  G.711 A-law and mu-law; an offer is answered with the first of its list we have.
 * hassmic hands the room as 16 kHz frames of 20 ms and takes the far side's the same way: halved and doubled here
 * (codec.c).
 *
 * The plugin's life (sdk/hassmic_plugin.h): one thread, callbacks; tick about once a second, and a helper thread that
 * only writes a byte into a pipe every 100 ms (the loop watches it: SIP's timers want T1/5).  MIT (LICENSE). */
#define _GNU_SOURCE
#include "hassmic_plugin.h"
#include "codec.h"
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define UA          "hassmic-sip"
#define SIP_MAX     4000                /* a message, ours or theirs */
#define T1          500
#define T2          4000
#define TX_MS       (64 * T1)           /* a transaction given up */
#define RINGBACK_MS 120000              /* a call out rings that long at most */
/* connected, no RTP for this long: the far end is gone.  Long: a phone with silence suppression sends nothing in its
 * pauses, and a PBX passes that on; the BYE is what ends a call, this only one whose far end vanished */
#define LOST_MS     120000
#define EXPIRES     600                 /* asked for; servers grant what they like */
#define RETRY_MS    60000               /* a registration that failed: again after a minute */
#define REFUSED_MS  300000              /* the password refused: every five minutes, never a flood of tries */
#define RESOLVE_MS  600000              /* the server's name looked up again, and the Echo's address with it */
#define TIMER_MS    100

enum { IDLE, CALLING, RINGING, LIVE };
static const char *const STATES[] = { "idle", "calling", "ringing", "connected" };
enum { LINE_OFF, LINE_CONNECTING, LINE_UP, LINE_REFUSED, LINE_UNREACHABLE };
static const char *const LINES[] = { "off", "connecting", "registered", "refused", "unreachable" };
enum { CODEC_PCMA, CODEC_PCMU };

/* ---------------------------------------------------------------- state */

static struct { char server[128], user[64], pass[128]; int port; } cfg = { "", "", "", 5060 };
static char reg_user[64], reg_pass[128];        /* what the registration was made with: a change registers again */
static uint32_t srv_ip; static int srv_port;    /* the server, resolved by hassmic (0: not yet) */
static char srv_ipt[16], local_ip[16] = "0.0.0.0";
static long long srv_at, resolve_retry;
static int sip_h = -1, sip_fd = -1, rtp_h = -1, rtp_fd = -1, sip_port, rtp_port, opened;
static int ready_done, timer_fd = -1;

struct tx { int on; char buf[SIP_MAX]; size_t n; char branch[40]; long long next, until; int iv; int answered; };

static struct {
    int line; char text[160]; int told;
    long long next;
    char call_id[48], tag[20]; uint32_t cseq;
    int auth_tries, granted;
    struct tx t;
} reg = { .told = -1 };

struct call {
    int state, outgoing, rang, acked, ok_sent, hassmic;     /* hassmic: it knows of the call (rings, calls, live) */
    char peer[96];
    char call_id[96], ltag[24];
    char lhdr[300], rhdr[300];          /* From/To of each side as sent in this dialog, tags included */
    char target[256];                   /* the far end's Contact */
    char route[4][256]; int nroute;
    uint32_t lcseq;
    /* a call here: what each response repeats of its INVITE */
    char vias[1200], ifrom[300], ito[300], icseq[64], ibranch[80];
    char resp[SIP_MAX]; size_t resp_n; long long resp_next, resp_until; int resp_iv;     /* a final response until its ACK */
    /* a call out: its INVITE (the ruri and the To before a tag, for CANCEL and ACK) */
    struct tx inv; char ruri[200], ito0[300]; uint32_t icseq_n; int auth_tries, cancel, cancel_sent;
    char ack[SIP_MAX]; size_t ack_n;    /* the ACK of a 2xx, again for each 2xx again */
    /* media */
    int codec, pt; uint32_t rtp_ip; int rtp_port, peered;
    uint16_t tx_seq; uint32_t tx_ts, ssrc; int first_tx;
    int rx_any; uint16_t rx_last; uint32_t rx_ext;
    struct half down, up;
    int16_t acc[2 * HM_CALL_FRAME]; int accn; uint32_t out_seq;     /* the far side's audio in 20 ms frames for hassmic */
    long long since, live_at, rx_at;
    unsigned rx, tx;
};
static struct call c, z;
static struct tx bye_tx, cancel_tx;    /* the BYE or CANCEL of a call that ended */

static long long now_ms(void)
{
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void cpy(char *d, size_t cap, const char *s)     /* bounded, cut where it does not fit */
{
    size_t n = strlen(s); if (n >= cap) n = cap - 1;
    memcpy(d, s, n); d[n] = 0;
}

static void rand_hex(char *out, int bytes)              /* bytes <= 16: hm_random gives 16 at a time */
{
    uint8_t r[16]; if (bytes > 16) bytes = 16;
    hm_random(r, (size_t)bytes);
    for (int i = 0; i < bytes; i++) sprintf(out + 2 * i, "%02x", r[i]);
}

static const char *ipstr(uint32_t a, char out[16])
{
    snprintf(out, 16, "%u.%u.%u.%u", a >> 24, (a >> 16) & 255, (a >> 8) & 255, a & 255);
    return out;
}

/* a bounded appender: what does not fit is cut (the message is then refused at the far end, never overrun here) */
struct out { char *p; size_t n, cap; };
static void put(struct out *o, const char *fmt, ...)
{
    va_list ap;
    if (o->n >= o->cap) return;
    va_start(ap, fmt); int k = vsnprintf(o->p + o->n, o->cap - o->n, fmt, ap); va_end(ap);
    if (k > 0) o->n = o->n + (size_t)k < o->cap ? o->n + (size_t)k : o->cap - 1;
}

/* ---------------------------------------------------------------- Home Assistant */

static void tell_state(void)
{
    hm_entity_state("phone", STATES[c.state]);
    hm_entity_state("phone_with", c.state ? c.peer : "");
}

static void result(const char *type) { hm_event("result", type); }

static void set_line(int line, const char *fmt, ...)
{
    va_list ap; char t[160];
    va_start(ap, fmt); vsnprintf(t, sizeof t, fmt, ap); va_end(ap);
    if (line != reg.line || strcmp(t, reg.text)) hm_log("line %s: %s", LINES[line], t);
    reg.line = line; cpy(reg.text, sizeof reg.text, t);
    if (ready_done && reg.told != line) { reg.told = line; hm_entity_state("line", LINES[line]); }
}

/* ---------------------------------------------------------------- the account */

static int user_ok(const char *s)               /* a SIP user part of the plain kind: what extensions are */
{
    if (!*s || strlen(s) >= sizeof cfg.user) return 0;
    for (const char *p = s; *p; p++) if (!isalnum((unsigned char)*p) && !strchr("._+-*#", *p)) return 0;
    return 1;
}

static int have_account(void) { return cfg.server[0] && user_ok(cfg.user); }

/* ---------------------------------------------------------------- messages */

struct msg {
    int req, code; char method[16], uri[256], reason[64];
    int nh; struct { const char *name; char *val; } h[64];
    const char *body; size_t blen;
    char raw[SIP_MAX + 1];
};

static const char *canon(const char *n)         /* RFC 3261 7.3.3's compact forms */
{
    static const char *const map[][2] = { { "v", "Via" }, { "f", "From" }, { "t", "To" }, { "i", "Call-ID" }, { "m", "Contact" },
                                          { "l", "Content-Length" }, { "c", "Content-Type" }, { "k", "Supported" } };
    for (size_t i = 0; i < sizeof map / sizeof map[0]; i++) if (!strcasecmp(n, map[i][0])) return map[i][1];
    return n;
}

static int parse(struct msg *m, const uint8_t *p, size_t n)
{
    char *s, *e, *line;
    if (n > SIP_MAX) return -1;
    memcpy(m->raw, p, n); m->raw[n] = 0; m->nh = 0; m->body = ""; m->blen = 0;
    s = m->raw;
    if (!(e = strstr(s, "\r\n"))) return -1;
    *e = 0; line = s; s = e + 2;
    if (!strncmp(line, "SIP/2.0 ", 8)) {
        m->req = 0; m->code = atoi(line + 8);
        const char *r = strchr(line + 8, ' ');
        cpy(m->reason, sizeof m->reason, r ? r + 1 : "");
        if (m->code < 100 || m->code > 699) return -1;
    } else {
        char ver[16];
        m->req = 1;
        if (sscanf(line, "%15s %255s %15s", m->method, m->uri, ver) != 3 || strcmp(ver, "SIP/2.0")) return -1;
    }
    while ((e = strstr(s, "\r\n"))) {
        *e = 0;
        if (!*s) { s = e + 2; break; }                  /* the end of the headers */
        char *colon = strchr(s, ':');
        if (colon && m->nh < (int)(sizeof m->h / sizeof m->h[0])) {
            char *ne = colon; while (ne > s && (ne[-1] == ' ' || ne[-1] == '\t')) ne--;
            *ne = 0; char *v = colon + 1; while (*v == ' ' || *v == '\t') v++;
            m->h[m->nh].name = canon(s); m->h[m->nh].val = v; m->nh++;
        }
        s = e + 2;
    }
    m->body = s; m->blen = strlen(s);
    for (int i = 0; i < m->nh; i++) if (!strcasecmp(m->h[i].name, "Content-Length")) {
        long l = atol(m->h[i].val); if (l >= 0 && (size_t)l < m->blen) m->blen = (size_t)l;
    }
    return 0;
}

static const char *hget(const struct msg *m, const char *name)
{
    for (int i = 0; i < m->nh; i++) if (!strcasecmp(m->h[i].name, name)) return m->h[i].val;
    return NULL;
}

/* a parameter of a header value (";branch=", ";tag=", ";expires="): after the URI's '>' where there is one */
static int param(const char *v, const char *name, char *out, size_t cap)
{
    size_t nl = strlen(name); const char *p = v ? strchr(v, '>') : NULL;
    if (!v) return -1;
    if (!p) p = v;
    for (p = strchr(p, ';'); p; p = strchr(p + 1, ';')) {
        const char *q = p + 1; while (*q == ' ') q++;
        if (!strncasecmp(q, name, nl) && (q[nl] == '=' || q[nl] == ';' || q[nl] == ',' || !q[nl] || q[nl] == ' ')) {
            q += nl; size_t k = 0;
            if (*q == '=') { q++; if (*q == '"') q++; while (*q && !strchr(";,\" >", *q) && k + 1 < cap) out[k++] = *q++; }
            out[k] = 0; return 0;
        }
    }
    return -1;
}

static void uri_of(const char *v, char *out, size_t cap)        /* the URI of a name-addr or addr-spec */
{
    const char *a = strchr(v, '<'), *b; size_t n;
    if (a) { a++; b = strchr(a, '>'); n = b ? (size_t)(b - a) : strlen(a); }
    else { a = v; n = strcspn(a, "; ,"); }
    if (n >= cap) n = cap - 1;
    memcpy(out, a, n); out[n] = 0;
}

static void uri_user(const char *uri, char *out, size_t cap)
{
    const char *p = !strncasecmp(uri, "sips:", 5) ? uri + 5 : !strncasecmp(uri, "sip:", 4) ? uri + 4 : uri;
    const char *at = strchr(p, '@'); size_t n = at ? (size_t)(at - p) : 0;
    if (n >= cap) n = cap - 1;
    memcpy(out, p, n); out[n] = 0;
}

static void display_of(const char *v, char *out, size_t cap)    /* '"Portero" <sip:60@...>' -> Portero */
{
    size_t k = 0;
    while (*v == ' ') v++;
    if (*v == '"') { v++; while (*v && *v != '"' && k + 1 < cap) { if (*v == '\\' && v[1]) v++; out[k++] = *v++; } }
    else { const char *a = strchr(v, '<'); if (a) while (v < a && k + 1 < cap) out[k++] = *v++; }
    while (k && out[k - 1] == ' ') k--;
    out[k] = 0;
}

/* For Home Assistant and the log: control characters out, and anything that is not 7-bit (hassmic refuses a state
 * that is not strict UTF-8; a caller id from a PBX may be Latin-1) */
static void clean(char *s)
{
    for (unsigned char *p = (unsigned char *)s; *p; p++) if (*p < 0x20 || *p == '"' || *p == '\\' || *p >= 0x7f) *p = ' ';
}

static void peer_of(const char *hdr, char *out, size_t cap)     /* "Portero (60)", or "60" */
{
    char d[64], u[64], uri[200];
    display_of(hdr, d, sizeof d); uri_of(hdr, uri, sizeof uri); uri_user(uri, u, sizeof u);
    struct out o = { out, 0, cap };
    out[0] = 0;
    if (d[0] && strcmp(d, u)) put(&o, "%s%s%s%s", d, u[0] ? " (" : "", u, u[0] ? ")" : "");
    else cpy(out, cap, u[0] ? u : d[0] ? d : "unknown");
    clean(out);
}

/* everything goes to the server: the SIP socket's peer is fixed to it */
static void sip_send(const char *buf, size_t n)
{
    static long long logged;
    if (sip_fd < 0) return;
    if (send(sip_fd, buf, n, MSG_DONTWAIT | MSG_NOSIGNAL) < 0 && errno != EAGAIN && errno != ECONNREFUSED && now_ms() - logged > 5000) {
        logged = now_ms(); hm_log("send: %s", strerror(errno));
    }
}

/* ---------------------------------------------------------------- SDP */

static size_t sdp(char *out, size_t cap, int answer_pt, int codec)     /* answer_pt < 0: our offer */
{
    static unsigned ver;
    struct out o = { out, 0, cap };
    put(&o, "v=0\r\no=hassmic 1 %u IN IP4 %s\r\ns=hassmic\r\nc=IN IP4 %s\r\nt=0 0\r\n", ++ver, local_ip, local_ip);
    if (answer_pt < 0) put(&o, "m=audio %d RTP/AVP 8 0\r\n", rtp_port);
    else put(&o, "m=audio %d RTP/AVP %d\r\n", rtp_port, answer_pt);
    if (answer_pt < 0 || codec == CODEC_PCMA) put(&o, "a=rtpmap:8 PCMA/8000\r\n");
    if (answer_pt < 0 || codec == CODEC_PCMU) put(&o, "a=rtpmap:0 PCMU/8000\r\n");
    put(&o, "a=ptime:20\r\na=sendrecv\r\n");
    return o.n;
}

/* The first codec of an offer's (or answer's) list we have: its payload type, codec, and where its RTP goes.  -1: none */
static int sdp_pick(const char *body, int *codec, uint32_t *ip_out, int *port_out)
{
    char ip[64] = "", mip[64] = "", line[300]; unsigned port = 0; int pts[32], npt = 0, in_audio = 0;
    struct { int pt; char name[32]; int rate; } map[32]; int nmap = 0;
    struct in_addr a;
    for (const char *p = body; *p; ) {
        size_t n = strcspn(p, "\r\n"); if (n >= sizeof line) n = sizeof line - 1;
        memcpy(line, p, n); line[n] = 0; p += n; while (*p == '\r' || *p == '\n') p++;
        if (!strncmp(line, "m=", 2)) {
            in_audio = !strncmp(line, "m=audio ", 8);
            if (in_audio && !npt) {
                char *q = line + 8, *e; port = (unsigned)strtoul(q, &e, 10); q = e;
                while (*q == ' ') q++;
                q += strcspn(q, " ");                   /* RTP/AVP */
                while (*q && npt < 32) { long v = strtol(q, &e, 10); if (e == q) break; pts[npt++] = (int)v; q = e; }
            }
        } else if (!strncmp(line, "c=IN IP4 ", 9)) snprintf(in_audio ? mip : ip, sizeof ip, "%.*s", (int)strcspn(line + 9, " /"), line + 9);
        else if (in_audio && !strncmp(line, "a=rtpmap:", 9) && nmap < 32) {
            char nm[32]; int pt, rate;
            if (sscanf(line + 9, "%d %31[^/]/%d", &pt, nm, &rate) == 3) { map[nmap].pt = pt; cpy(map[nmap].name, sizeof map[0].name, nm); map[nmap].rate = rate; nmap++; }
        }
    }
    if (!port || port > 65535 || !npt || inet_pton(AF_INET, mip[0] ? mip : ip, &a) != 1) return -1;
    *ip_out = ntohl(a.s_addr); *port_out = (int)port;
    for (int i = 0; i < npt; i++) {
        int pt = pts[i], k = -1;
        for (int j = 0; j < nmap; j++) if (map[j].pt == pt) k = j;
        if (pt == 8 && (k < 0 || !strcasecmp(map[k].name, "PCMA"))) { *codec = CODEC_PCMA; return pt; }
        if (pt == 0 && (k < 0 || !strcasecmp(map[k].name, "PCMU"))) { *codec = CODEC_PCMU; return pt; }
        if (k >= 0 && !strcasecmp(map[k].name, "PCMA") && map[k].rate == 8000) { *codec = CODEC_PCMA; return pt; }
        if (k >= 0 && !strcasecmp(map[k].name, "PCMU") && map[k].rate == 8000) { *codec = CODEC_PCMU; return pt; }
    }
    return -1;
}

/* ---------------------------------------------------------------- digest authentication */

/* The answer to a challenge (WWW-Authenticate or Proxy-Authenticate) as its header line, "" if it cannot be met */
static void digest(const char *chal, int proxy, const char *method, const char *uri, char *out, size_t cap)
{
    char realm[128] = "", nonce[160] = "", opaque[160] = "", algo[32] = "MD5", qop[64] = "", cnonce[17], ha1[33], ha2[33], resp[33], in[700];
    uint8_t d[16]; int n;
    out[0] = 0;
    if (!chal || strncasecmp(chal, "Digest", 6)) return;
    /* the challenge's parameters: name="value" or name=value, comma separated (param() reads ';', so by hand) */
    for (const char *p = chal + 6; *p; ) {
        while (*p == ' ' || *p == ',') p++;
        const char *eq = strchr(p, '='); if (!eq) break;
        char name[32]; size_t nl = (size_t)(eq - p); if (nl >= sizeof name) nl = sizeof name - 1;
        memcpy(name, p, nl); name[nl] = 0; while (nl && name[nl - 1] == ' ') name[--nl] = 0;
        p = eq + 1; while (*p == ' ') p++;
        char v[200]; size_t k = 0;
        if (*p == '"') { p++; while (*p && *p != '"') { if (*p == '\\' && p[1]) p++; if (k + 1 < sizeof v) v[k++] = *p; p++; } if (*p) p++; }
        else while (*p && *p != ',') { if (k + 1 < sizeof v) v[k++] = *p; p++; }
        while (k && v[k - 1] == ' ') k--;
        v[k] = 0;
        if (!strcasecmp(name, "realm")) cpy(realm, sizeof realm, v);
        else if (!strcasecmp(name, "nonce")) cpy(nonce, sizeof nonce, v);
        else if (!strcasecmp(name, "opaque")) cpy(opaque, sizeof opaque, v);
        else if (!strcasecmp(name, "algorithm")) cpy(algo, sizeof algo, v);
        else if (!strcasecmp(name, "qop")) cpy(qop, sizeof qop, v);
    }
    if (strcasecmp(algo, "MD5") || !nonce[0]) return;           /* MD5-sess, SHA-256: not ours */
    int auth = 0;                                               /* qop="auth,auth-int": auth */
    { char *sv = NULL; for (char *t = strtok_r(qop, ", ", &sv); t; t = strtok_r(NULL, ", ", &sv)) auth |= !strcasecmp(t, "auth"); }
    n = snprintf(in, sizeof in, "%s:%s:%s", cfg.user, realm, cfg.pass);
    md5(in, (size_t)n, d); for (int i = 0; i < 16; i++) sprintf(ha1 + 2 * i, "%02x", d[i]);
    memset(in, 0, sizeof in);
    n = snprintf(in, sizeof in, "%s:%s", method, uri);
    md5(in, (size_t)n, d); for (int i = 0; i < 16; i++) sprintf(ha2 + 2 * i, "%02x", d[i]);
    rand_hex(cnonce, 8);
    if (auth) n = snprintf(in, sizeof in, "%s:%s:00000001:%s:auth:%s", ha1, nonce, cnonce, ha2);
    else n = snprintf(in, sizeof in, "%s:%s:%s", ha1, nonce, ha2);
    md5(in, (size_t)n, d); for (int i = 0; i < 16; i++) sprintf(resp + 2 * i, "%02x", d[i]);
    memset(ha1, 0, sizeof ha1);
    struct out o = { out, 0, cap };
    put(&o, "%s: Digest username=\"%s\", realm=\"%s\", nonce=\"%s\", uri=\"%s\", response=\"%s\", algorithm=MD5",
        proxy ? "Proxy-Authorization" : "Authorization", cfg.user, realm, nonce, uri, resp);
    if (opaque[0]) put(&o, ", opaque=\"%s\"", opaque);
    if (auth) put(&o, ", qop=auth, nc=00000001, cnonce=\"%s\"", cnonce);
    put(&o, "\r\n");
}

/* ---------------------------------------------------------------- building requests */

static void tx_start(struct tx *t, long long now)
{
    t->on = 1; t->answered = 0; t->iv = T1; t->next = now + T1; t->until = now + TX_MS;
    sip_send(t->buf, t->n);
}

/* A request into t->buf: method, request URI, From/To/Call-ID/CSeq as given, a fresh branch; extra: whole header lines */
static void build(struct tx *t, const char *method, const char *ruri, const char *from, const char *to, const char *call_id,
                  uint32_t cseq, const char *cseq_method, char routes[][256], int nroute, const char *extra, const char *ctype, const char *body)
{
    struct out o = { t->buf, 0, sizeof t->buf };
    strcpy(t->branch, "z9hG4bK"); rand_hex(t->branch + 7, 10);
    put(&o, "%s %s SIP/2.0\r\nVia: SIP/2.0/UDP %s:%d;rport;branch=%s\r\nMax-Forwards: 70\r\n", method, ruri, local_ip, sip_port, t->branch);
    for (int i = 0; i < nroute; i++) put(&o, "Route: %s\r\n", routes[i]);
    put(&o, "From: %s\r\nTo: %s\r\nCall-ID: %s\r\nCSeq: %u %s\r\n", from, to, call_id, cseq, cseq_method ? cseq_method : method);
    if (extra) put(&o, "%s", extra);
    put(&o, "User-Agent: " UA "\r\n");
    if (body && *body) put(&o, "Content-Type: %s\r\nContent-Length: %zu\r\n\r\n%s", ctype, strlen(body), body);
    else put(&o, "Content-Length: 0\r\n\r\n");
    t->n = o.n;
}

static void contact(char *out, size_t cap)
{
    snprintf(out, cap, "Contact: <sip:%s@%s:%d>\r\n", cfg.user, local_ip, sip_port);
}

static const char ALLOW[] = "Allow: INVITE, ACK, CANCEL, BYE, OPTIONS\r\n";

/* ---------------------------------------------------------------- registration */

static void reg_send(long long now, const char *auth, int expires)
{
    char from[300], uri[160], extra[1200], ct[200], to[300];
    snprintf(uri, sizeof uri, "sip:%s", cfg.server);
    snprintf(from, sizeof from, "<sip:%s@%s>;tag=%s", cfg.user, cfg.server, reg.tag);
    contact(ct, sizeof ct);
    snprintf(extra, sizeof extra, "%s%sExpires: %d\r\n%s", ct, ALLOW, expires, auth ? auth : "");
    snprintf(to, sizeof to, "<sip:%s@%s>", cfg.user, cfg.server);
    build(&reg.t, "REGISTER", uri, from, to, reg.call_id, ++reg.cseq, NULL, NULL, 0, extra, NULL, NULL);
    tx_start(&reg.t, now);
}

static void unregister(void)                    /* once, best effort, as the binding would expire anyway */
{
    if (reg.line != LINE_UP || sip_fd < 0) return;
    reg_send(now_ms(), NULL, 0); reg.t.on = 0;
}

static void on_register(const struct msg *m, long long now)
{
    if (m->code < 200) return;
    reg.t.on = 0;
    if ((m->code == 401 || m->code == 407) && reg.auth_tries < 2 && cfg.pass[0]) {
        char auth[900], uri[160];
        snprintf(uri, sizeof uri, "sip:%s", cfg.server);
        digest(hget(m, m->code == 401 ? "WWW-Authenticate" : "Proxy-Authenticate"), m->code == 407, "REGISTER", uri, auth, sizeof auth);
        if (auth[0]) { reg.auth_tries++; reg_send(now, auth, reg.granted > EXPIRES ? reg.granted : EXPIRES); memset(auth, 0, sizeof auth); return; }
    }
    if (m->code == 423) {                       /* Interval Too Brief */
        const char *me = hget(m, "Min-Expires");
        int v = me ? atoi(me) : 0;
        if (v > 0 && v < 86400) { reg.granted = v; reg.next = now; return; }
    }
    if (m->code >= 200 && m->code < 300) {
        int exp = EXPIRES; char e[16];
        const char *h = hget(m, "Expires"); if (h) exp = atoi(h);
        for (int i = 0; i < m->nh; i++) if (!strcasecmp(m->h[i].name, "Contact") && strstr(m->h[i].val, local_ip) && !param(m->h[i].val, "expires", e, sizeof e)) exp = atoi(e);
        if (exp < 30) exp = 30;
        reg.next = now + (long long)exp * 800;                 /* at 80 % */
        reg.auth_tries = 0;
        set_line(LINE_UP, "registered at %s as %s (%d s)", cfg.server, cfg.user, exp);
        return;
    }
    char why[80]; cpy(why, sizeof why, m->reason); clean(why);
    if (m->code == 401 || m->code == 403 || m->code == 407) {
        reg.next = now + REFUSED_MS;
        set_line(LINE_REFUSED, "%s refused %s: %d %s", cfg.server, cfg.user, m->code, why);
    } else {
        reg.next = now + RETRY_MS;
        set_line(LINE_UNREACHABLE, "%s answered %d %s", cfg.server, m->code, why);
    }
    reg.auth_tries = 0;
}

/* ---------------------------------------------------------------- calls */

static void reset_call(struct call *k) { memset(k, 0, sizeof *k); }

/* The far side is connected: the media begins (its peer fixed to what the SDP named), hassmic told */
static void go_live(long long now)
{
    int why = 0; uint8_t r[10];
    c.state = LIVE; c.live_at = c.rx_at = now;
    hm_random(r, sizeof r);
    c.tx_seq = (uint16_t)(r[0] << 8 | r[1]); c.tx_ts = (uint32_t)r[2] << 24 | r[3] << 16 | r[4] << 8 | r[5];
    c.ssrc = (uint32_t)r[6] << 24 | r[7] << 16 | r[8] << 8 | r[9];
    c.first_tx = 1; c.rx_any = 0; c.accn = 0; c.out_seq = 0;
    memset(&c.down, 0, sizeof c.down); memset(&c.up, 0, sizeof c.up);
    c.peered = hm_udp_peer(rtp_h, c.rtp_ip, c.rtp_port) == 0;
    if (!c.peered) hm_log("the far side's audio address is refused by hassmic: no sound this call");
    if (hm_call_live("", &why)) hm_log("hassmic refused the call as live (%d)", why);
    tell_state();
    if (c.outgoing) result("connected");
    hm_log("connected with %s (%s)", c.peer, c.codec == CODEC_PCMA ? "G.711 A-law" : "G.711 mu-law");
}

/* The call over here, whatever ended it: Home Assistant told, hassmic told where it does not know yet (TELL: the
 * plugin's own end, never after hassmic's CALL_HANGUP), what its transactions still owe moved to z.  TYPE: Phone
 * result's event for a call going out that ended before it was live (NULL: none) */
static void end_call(const char *why, const char *type, int tell)
{
    if (c.state == IDLE) return;
    if (c.state == LIVE) hm_log("ended with %s after %lld s (%s); %u packets in, %u out", c.peer, (now_ms() - c.live_at) / 1000, why, c.rx, c.tx);
    else hm_log("%s %s: %s", c.outgoing ? "call to" : "call from", c.peer, why);
    if (tell && c.hassmic) {
        int r = !strcmp(type ? type : "", "busy") ? HM_RESULT_BUSY : !strcmp(type ? type : "", "declined") ? HM_RESULT_DECLINED
              : !strcmp(type ? type : "", "not_answered") ? HM_RESULT_NO_ANSWER
              : type && strcmp(type, "cancelled") ? HM_RESULT_FAILED : HM_RESULT_HUNG_UP;
        hm_call_end(r);
    }
    if (type && c.outgoing && c.state == CALLING) result(type);
    z = c; z.state = IDLE; z.since = now_ms();
    reset_call(&c);
    tell_state();
}

static void dialog_request(struct tx *t, const char *method, const char *extra)    /* BYE in c's dialog */
{
    build(t, method, c.target[0] ? c.target : c.ruri, c.lhdr, c.rhdr, c.call_id, ++c.lcseq, NULL, c.route, c.nroute, extra, NULL, NULL);
}

static void send_bye(const char *why)
{
    char extra[160]; snprintf(extra, sizeof extra, "Reason: Q.850;cause=16;text=\"%.80s\"\r\n", why);
    dialog_request(&bye_tx, "BYE", extra);
    tx_start(&bye_tx, now_ms());
}

/* ---- a call here: responses to its INVITE */

static size_t response(char *out, size_t cap, int code, const char *reason, const struct call *k, const char *body)
{
    struct out o = { out, 0, cap };
    put(&o, "SIP/2.0 %d %s\r\n%sFrom: %s\r\nTo: %s", code, reason, k->vias, k->ifrom, k->ito);
    if (code > 100 && !strstr(k->ito, "tag=")) put(&o, ";tag=%s", k->ltag);
    put(&o, "\r\nCall-ID: %s\r\nCSeq: %s\r\n", k->call_id, k->icseq);
    if (code >= 180 && code < 300) { char ct[200]; contact(ct, sizeof ct); put(&o, "%s%s", ct, ALLOW); }
    put(&o, "User-Agent: " UA "\r\n");
    if (body && *body) put(&o, "Content-Type: application/sdp\r\nContent-Length: %zu\r\n\r\n%s", strlen(body), body);
    else put(&o, "Content-Length: 0\r\n\r\n");
    return o.n;
}

static void respond_invite(int code, const char *reason, const char *body)     /* c's INVITE; a final one until its ACK */
{
    c.resp_n = response(c.resp, sizeof c.resp, code, reason, &c, body);
    sip_send(c.resp, c.resp_n);
    if (code >= 200) { long long now = now_ms(); c.resp_iv = T1; c.resp_next = now + T1; c.resp_until = now + TX_MS; c.acked = 0; }
}

/* The Vias of a request as its responses repeat them: the top one with where it came from (RFC 3581) */
static void vias_of(const struct msg *m, const struct sockaddr_in *from, char *out, size_t cap)
{
    struct out o = { out, 0, cap }; int top = 1;
    out[0] = 0;
    for (int i = 0; i < m->nh; i++) {
        if (strcasecmp(m->h[i].name, "Via")) continue;
        if (top) {
            char ip[16]; inet_ntop(AF_INET, &from->sin_addr, ip, sizeof ip);
            const char *rp = strcasestr(m->h[i].val, ";rport");
            if (rp && (rp[6] == ';' || !rp[6] || rp[6] == ',')) put(&o, "Via: %.*s;rport=%u;received=%s%s\r\n", (int)(rp - m->h[i].val), m->h[i].val, ntohs(from->sin_port), ip, rp + 6);
            else put(&o, "Via: %s;received=%s\r\n", m->h[i].val, ip);
            top = 0;
        } else put(&o, "Via: %s\r\n", m->h[i].val);
    }
}

static void reply(const struct msg *m, const struct sockaddr_in *from, int code, const char *reason, const char *extra)  /* outside a call */
{
    char buf[SIP_MAX], vias[1200], tag[24]; struct out o = { buf, 0, sizeof buf };
    const char *to = hget(m, "To");
    vias_of(m, from, vias, sizeof vias); rand_hex(tag, 6);
    put(&o, "SIP/2.0 %d %s\r\n%sFrom: %s\r\nTo: %s", code, reason, vias, hget(m, "From") ? hget(m, "From") : "", to ? to : "");
    if (to && !strstr(to, "tag=")) put(&o, ";tag=%s", tag);
    put(&o, "\r\nCall-ID: %s\r\nCSeq: %s\r\n%sUser-Agent: " UA "\r\n%sContent-Length: 0\r\n\r\n", hget(m, "Call-ID") ? hget(m, "Call-ID") : "",
        hget(m, "CSeq") ? hget(m, "CSeq") : "", ALLOW, extra ? extra : "");
    sip_send(buf, o.n);
}

static void route_set(const struct msg *m, struct call *k, int reverse)        /* Record-Route, as Route headers */
{
    char all[8][256]; int n = 0;
    for (int i = 0; i < m->nh && n < 8; i++) {
        if (strcasecmp(m->h[i].name, "Record-Route")) continue;
        for (const char *p = m->h[i].val; *p && n < 8; ) {          /* "<a>, <b>" */
            while (*p == ' ' || *p == ',') p++;
            const char *a = strchr(p, '<'), *b = a ? strchr(a, '>') : NULL;
            if (!a || !b) break;
            snprintf(all[n++], sizeof all[0], "%.*s", (int)(b - a + 1), a);
            p = b + 1;
        }
    }
    k->nroute = n < 4 ? n : 4;
    for (int i = 0; i < k->nroute; i++) memcpy(k->route[i], all[reverse ? n - 1 - i : i], sizeof k->route[0]);
}

/* a final refusal of an INVITE that is no call of ours: kept in z until its ACK */
static void refuse(struct call *k, int code, const char *reason, long long now)
{
    z = *k; z.since = now;
    z.resp_n = response(z.resp, sizeof z.resp, code, reason, &z, NULL);
    sip_send(z.resp, z.resp_n);
    z.resp_iv = T1; z.resp_next = now + T1; z.resp_until = now + TX_MS; z.acked = 0;
}

static void on_invite(const struct msg *m, const struct sockaddr_in *from, long long now)
{
    char branch[80] = "", tag[64] = "";
    const char *cid = hget(m, "Call-ID"), *to = hget(m, "To"), *fr = hget(m, "From"), *cs = hget(m, "CSeq");
    param(hget(m, "Via"), "branch", branch, sizeof branch);
    if (!cid || !to || !fr || !cs) { reply(m, from, 400, "Bad Request", NULL); return; }
    param(to, "tag", tag, sizeof tag);
    if (c.state && !strcmp(c.call_id, cid)) {
        if (!strcmp(c.ibranch, branch)) { if (c.resp_n) sip_send(c.resp, c.resp_n); return; }    /* again */
        if (tag[0] && !strcmp(tag, c.ltag) && c.state == LIVE) {           /* a re-INVITE: the same answer */
            char body[1200]; int codec, port; uint32_t ip;
            if (m->blen && sdp_pick(m->body, &codec, &ip, &port) >= 0 && codec == c.codec && (ip != c.rtp_ip || port != c.rtp_port)) {
                c.rtp_ip = ip; c.rtp_port = port; c.peered = hm_udp_peer(rtp_h, ip, port) == 0;
            }
            sdp(body, sizeof body, c.pt, c.codec);
            struct call *k = malloc(sizeof *k);
            if (!k) return;
            *k = c; cpy(k->ibranch, sizeof k->ibranch, branch);
            vias_of(m, from, k->vias, sizeof k->vias); cpy(k->ifrom, sizeof k->ifrom, fr); cpy(k->ito, sizeof k->ito, to);
            cpy(k->icseq, sizeof k->icseq, cs);
            char buf[SIP_MAX]; size_t n = response(buf, sizeof buf, 200, "OK", k, body);
            free(k);
            sip_send(buf, n);
            return;
        }
    }
    if (z.call_id[0] && !strcmp(z.call_id, cid) && !strcmp(z.ibranch, branch)) { if (z.resp_n) sip_send(z.resp, z.resp_n); return; }
    if (tag[0]) { reply(m, from, 481, "Call/Transaction Does Not Exist", NULL); return; }
    /* a new call */
    static struct call k;
    reset_call(&k);
    cpy(k.call_id, sizeof k.call_id, cid); rand_hex(k.ltag, 8);
    cpy(k.ibranch, sizeof k.ibranch, branch);
    vias_of(m, from, k.vias, sizeof k.vias);
    cpy(k.ifrom, sizeof k.ifrom, fr); cpy(k.ito, sizeof k.ito, to); cpy(k.icseq, sizeof k.icseq, cs);
    peer_of(fr, k.peer, sizeof k.peer);
    if (c.state) { hm_log("call from %s refused: busy in another call", k.peer); refuse(&k, 486, "Busy Here", now); return; }
    if (!m->blen || (k.pt = sdp_pick(m->body, &k.codec, &k.rtp_ip, &k.rtp_port)) < 0) {
        hm_log("call from %s refused: no codec in common (G.711 only)", k.peer); refuse(&k, 488, "Not Acceptable Here", now); return;
    }
    int why = 0;
    if (hm_call_incoming(k.peer, &why)) {
        const char *w = why == HM_ERR_BUSY ? "busy (a Drop In, or another call)" : why == HM_ERR_DENIED ? "do not disturb, or the microphones are off"
                      : why == HM_ERR_LIMIT ? "three rings unanswered in ten minutes" : "refused by hassmic";
        hm_log("call from %s refused: %s", k.peer, w);
        refuse(&k, why == HM_ERR_BUSY ? 486 : 480, why == HM_ERR_BUSY ? "Busy Here" : "Temporarily Unavailable", now);
        return;
    }
    snprintf(k.lhdr, sizeof k.lhdr, "%s;tag=%s", to, k.ltag); cpy(k.rhdr, sizeof k.rhdr, fr);
    { const char *ct = hget(m, "Contact"); if (ct) uri_of(ct, k.target, sizeof k.target); }
    route_set(m, &k, 0);
    k.lcseq = 1; k.state = RINGING; k.since = now; k.hassmic = 1;
    c = k;
    respond_invite(100, "Trying", NULL);
    respond_invite(180, "Ringing", NULL);
    hm_log("%s calls, ringing", c.peer);
    tell_state();
}

static void on_ack(const struct msg *m)
{
    const char *cid = hget(m, "Call-ID");
    if (!cid) return;
    if (c.call_id[0] && !strcmp(c.call_id, cid)) { c.acked = 1; c.resp_until = 0; }
    if (z.call_id[0] && !strcmp(z.call_id, cid)) { z.acked = 1; z.resp_until = 0; }
}

static void on_cancel(const struct msg *m, const struct sockaddr_in *from)
{
    char branch[80] = ""; const char *cid = hget(m, "Call-ID");
    int theirs = cid && z.call_id[0] && !strcmp(z.call_id, cid);
    param(hget(m, "Via"), "branch", branch, sizeof branch);
    if (!cid || strcmp(c.call_id, cid) || strcmp(c.ibranch, branch) || c.state != RINGING) {
        reply(m, from, theirs ? 200 : 481, theirs ? "OK" : "Call/Transaction Does Not Exist", NULL);
        return;
    }
    reply(m, from, 200, "OK", NULL);
    respond_invite(487, "Request Terminated", NULL);
    end_call("the caller hung up", "cancelled", 1);
}

static void on_bye(const struct msg *m, const struct sockaddr_in *from)
{
    const char *cid = hget(m, "Call-ID");
    int theirs = cid && z.call_id[0] && !strcmp(z.call_id, cid);
    if (!cid || strcmp(c.call_id, cid) || c.state == IDLE) { reply(m, from, theirs ? 200 : 481, theirs ? "OK" : "Call/Transaction Does Not Exist", NULL); return; }
    reply(m, from, 200, "OK", NULL);
    char why[160]; snprintf(why, sizeof why, "%s hung up", c.peer);
    end_call(why, NULL, 1);
}

/* ---- a call out */

static void invite_send(long long now, const char *auth)
{
    char extra[1600], ct[200], body[1200];
    contact(ct, sizeof ct);
    snprintf(extra, sizeof extra, "%s%s%s", ct, ALLOW, auth ? auth : "");
    sdp(body, sizeof body, -1, 0);
    c.icseq_n = ++c.lcseq;
    build(&c.inv, "INVITE", c.ruri, c.lhdr, c.ito0, c.call_id, c.icseq_n, NULL, NULL, 0, extra, "application/sdp", body);
    tx_start(&c.inv, now);
}

/* The ACK of a final non-2xx response: the INVITE's branch and request URI, the response's To (RFC 3261 17.1.1.3) */
static void ack_failure(const struct call *k, const struct msg *m)
{
    char buf[SIP_MAX]; struct out o = { buf, 0, sizeof buf };
    put(&o, "ACK %s SIP/2.0\r\nVia: SIP/2.0/UDP %s:%d;rport;branch=%s\r\nMax-Forwards: 70\r\nFrom: %s\r\nTo: %s\r\nCall-ID: %s\r\n"
        "CSeq: %u ACK\r\nContent-Length: 0\r\n\r\n", k->ruri, local_ip, sip_port, k->inv.branch, k->lhdr, hget(m, "To") ? hget(m, "To") : k->ito0,
        k->call_id, k->icseq_n);
    sip_send(buf, o.n);
}

static void cancel_send(const struct call *k)
{
    struct out o = { cancel_tx.buf, 0, sizeof cancel_tx.buf };
    cpy(cancel_tx.branch, sizeof cancel_tx.branch, k->inv.branch);
    put(&o, "CANCEL %s SIP/2.0\r\nVia: SIP/2.0/UDP %s:%d;rport;branch=%s\r\nMax-Forwards: 70\r\nFrom: %s\r\nTo: %s\r\nCall-ID: %s\r\n"
        "CSeq: %u CANCEL\r\nUser-Agent: " UA "\r\nContent-Length: 0\r\n\r\n", k->ruri, local_ip, sip_port, cancel_tx.branch, k->lhdr, k->ito0,
        k->call_id, k->icseq_n);
    cancel_tx.n = o.n;
    tx_start(&cancel_tx, now_ms());
}

static const char *result_of(int code)
{
    switch (code) {
    case 486: case 600: return "busy";
    case 603: case 403: return "declined";
    case 404: case 484: case 604: return "not_found";
    case 408: case 480: case 487: return "not_answered";     /* nobody took it, or it was unreachable */
    default: return "failed";
    }
}

/* A response to the INVITE of k (c, or z: a call that ended here) */
static void on_invite_response(struct call *k, const struct msg *m, long long now)
{
    int live = k == &c;
    if (m->code < 200) {
        k->inv.answered = 1;
        if (k->cancel && !k->cancel_sent) { k->cancel_sent = 1; cancel_send(k); }
        if (live && m->code >= 180 && !k->rang) { k->rang = 1; k->since = now; hm_log("%s rings", k->peer); hm_call_ringing_there(); result("ringing"); }
        return;
    }
    if (m->code >= 300) {
        k->inv.on = 0;
        ack_failure(k, m);
        if (live && (m->code == 401 || m->code == 407) && k->auth_tries < 1 && cfg.pass[0]) {
            char auth[900];
            digest(hget(m, m->code == 401 ? "WWW-Authenticate" : "Proxy-Authenticate"), m->code == 407, "INVITE", k->ruri, auth, sizeof auth);
            if (auth[0]) { k->auth_tries++; invite_send(now, auth); memset(auth, 0, sizeof auth); return; }
        }
        if (live) { char why[100]; snprintf(why, sizeof why, "%d %s", m->code, m->reason); clean(why); end_call(why, result_of(m->code), 1); }
        return;
    }
    /* 2xx: the dialog */
    k->inv.on = 0;
    if (k->ack_n) { sip_send(k->ack, k->ack_n); return; }               /* a 2xx again: our ACK again */
    { const char *to = hget(m, "To"); if (to) cpy(k->rhdr, sizeof k->rhdr, to); }
    { const char *ct = hget(m, "Contact"); if (ct) uri_of(ct, k->target, sizeof k->target); }
    route_set(m, k, 1);
    static struct tx a;
    memset(&a, 0, sizeof a);
    build(&a, "ACK", k->target[0] ? k->target : k->ruri, k->lhdr, k->rhdr, k->call_id, k->icseq_n, "ACK", k->route, k->nroute, NULL, NULL, NULL);
    memcpy(k->ack, a.buf, a.n); k->ack_n = a.n;
    sip_send(k->ack, k->ack_n);
    if (!live || k->cancel) {                       /* answered after we hung up: over at once */
        static struct call keep;
        keep = c; c = *k;
        send_bye("hung up before the answer came");
        *k = c; c = keep;
        return;
    }
    int codec, port; uint32_t ip; int pt = m->blen ? sdp_pick(m->body, &codec, &ip, &port) : -1;
    if (pt < 0) { send_bye("no codec in common"); end_call("no codec in common in the answer", "failed", 1); return; }
    k->pt = pt; k->codec = codec; k->rtp_ip = ip; k->rtp_port = port;
    go_live(now);
}

/* ---------------------------------------------------------------- the media (RTP) */

/* call: one frame of the room (20 ms at 16 kHz), from hassmic's call processing: 8 kHz G.711, one RTP packet */
static void on_call_audio(void *ctx, uint32_t seq, const int16_t *pcm, int n)
{
    uint8_t pkt[12 + HM_CALL_FRAME / 2]; int16_t h[HM_CALL_FRAME / 2];
    (void)ctx; (void)seq;
    if (c.state != LIVE || !c.peered || n != HM_CALL_FRAME) return;
    half_down(&c.down, pcm, HM_CALL_FRAME, h);
    pkt[0] = 0x80; pkt[1] = (uint8_t)(c.pt | (c.first_tx ? 0x80 : 0));
    pkt[2] = (uint8_t)(c.tx_seq >> 8); pkt[3] = (uint8_t)c.tx_seq;
    for (int i = 0; i < 4; i++) { pkt[4 + i] = (uint8_t)(c.tx_ts >> (24 - 8 * i)); pkt[8 + i] = (uint8_t)(c.ssrc >> (24 - 8 * i)); }
    for (int i = 0; i < HM_CALL_FRAME / 2; i++) pkt[12 + i] = c.codec == CODEC_PCMA ? g711_alaw(h[i]) : g711_ulaw(h[i]);
    c.tx_seq++; c.tx_ts += HM_CALL_FRAME / 2; c.first_tx = 0;
    if (send(rtp_fd, pkt, sizeof pkt, MSG_DONTWAIT | MSG_NOSIGNAL) == (ssize_t)sizeof pkt) c.tx++;
}

/* the far side's audio, decoded to 16 kHz, handed to hassmic in 20 ms frames; their numbers follow RTP's (widened to
 * 32 bits), so a packet lost on the way is a frame missing for hassmic's jitter buffer, which fades over it */
static void rtp_packet(const uint8_t *p, size_t n)
{
    size_t off = 12;
    if (n < 12 || (p[0] >> 6) != 2) return;
    off += (size_t)(p[0] & 0x0f) * 4;
    if (p[0] & 0x10) { if (n < off + 4) return; off += 4 + (size_t)(p[off + 2] << 8 | p[off + 3]) * 4; }
    if (p[0] & 0x20) { if (!n || p[n - 1] > n) return; n -= p[n - 1]; }
    if (n <= off || (p[1] & 0x7f) != c.pt) return;
    uint16_t seq = (uint16_t)(p[2] << 8 | p[3]); uint32_t ext;
    if (!c.rx_any) { c.rx_any = 1; c.rx_last = seq; c.rx_ext = ext = seq; }
    else { int16_t d = (int16_t)(seq - c.rx_last); ext = c.rx_ext + (uint32_t)(int32_t)d; if (d > 0) { c.rx_last = seq; c.rx_ext = ext; } }
    c.rx_at = now_ms(); c.rx++;
    size_t len = n - off;
    if (len > HM_CALL_FRAME / 2 * 4) return;            /* more than 80 ms in one packet: not what we asked for */
    int16_t h[HM_CALL_FRAME * 2], up[HM_CALL_FRAME * 4];
    for (size_t i = 0; i < len; i++) h[i] = c.codec == CODEC_PCMA ? g711_alaw_dec(p[off + i]) : g711_ulaw_dec(p[off + i]);
    half_up(&c.up, h, len, up);
    if (len == HM_CALL_FRAME / 2) { hm_call_audio(ext, up); return; }       /* 20 ms: one frame, numbered by RTP */
    /* another packet length: gathered into 20 ms frames of our own numbering */
    for (size_t i = 0; i < 2 * len; i++) {
        c.acc[c.accn++] = up[i];
        if (c.accn == HM_CALL_FRAME) { hm_call_audio(c.out_seq++, c.acc); c.accn = 0; }
    }
}

static void on_rtp_readable(void *ctx, int fd)
{
    uint8_t buf[1500];
    (void)ctx;
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof buf, MSG_DONTWAIT);
        if (n <= 0) break;
        if (c.state == LIVE) rtp_packet(buf, (size_t)n);
    }
}

/* ---------------------------------------------------------------- the network */

/* the server's address: hassmic resolves the owner's host (net.server) and connects a UDP socket there, whose peer is
 * that address; the Echo's own as the server sees it is the socket's name.  The socket itself is not used */
static int resolve(long long now)
{
    int why = 0, fd = hm_connect(0, 0, &why);
    struct sockaddr_in a, me; socklen_t l = sizeof a, ml = sizeof me;
    srv_at = now;
    if (fd < 0) {
        set_line(LINE_UNREACHABLE, why == HM_ERR_NOT_FOUND ? "cannot find %s" : why == HM_ERR_UNSET ? "no phone system entered (%s)"
                 : why == HM_ERR_DENIED ? "hassmic does not let plugins reach %s" : "cannot reach %s", cfg.server);
        resolve_retry = now + 30000;
        return -1;
    }
    int ok = !getpeername(fd, (struct sockaddr *)&a, &l) && !getsockname(fd, (struct sockaddr *)&me, &ml) && a.sin_family == AF_INET;
    close(fd);
    if (!ok) { resolve_retry = now + 30000; return -1; }
    uint32_t ip = ntohl(a.sin_addr.s_addr); int port = ntohs(a.sin_port);
    char lip[16]; inet_ntop(AF_INET, &me.sin_addr, lip, sizeof lip);
    if (ip != srv_ip || port != srv_port) {
        srv_ip = ip; srv_port = port; ipstr(ip, srv_ipt);
        if (sip_fd >= 0 && hm_udp_peer(sip_h, srv_ip, srv_port)) hm_log("hassmic refused %s as the SIP port's peer", srv_ipt);
        reg.next = now; reg.t.on = 0;
    }
    if (strcmp(lip, local_ip)) {
        if (strcmp(local_ip, "0.0.0.0")) hm_log("this Echo is at %s now, registering again", lip);
        cpy(local_ip, sizeof local_ip, lip); reg.next = now; reg.t.on = 0;
    }
    return 0;
}

static void on_sip_readable(void *ctx, int fd);

static int open_ports(void)
{
    int why = 0;
    if (opened) return 0;
    if (sip_fd < 0 && (sip_fd = hm_udp_open(HM_CAP_NET_UDP, 0, &sip_h, &sip_port, &why)) < 0) { hm_log("no SIP port (%d)", why); return -1; }
    if (rtp_fd < 0 && (rtp_fd = hm_udp_open(HM_CAP_NET_UDP, 1, &rtp_h, &rtp_port, &why)) < 0) { hm_log("no audio port (%d)", why); return -1; }
    if (hm_udp_peer(sip_h, srv_ip, srv_port)) { hm_log("hassmic refused %s as the SIP port's peer", srv_ipt); return -1; }
    hm_watch(sip_fd, on_sip_readable);
    hm_watch(rtp_fd, on_rtp_readable);
    opened = 1;
    hm_log("SIP on UDP %d, audio on %d, phone system %s:%d", sip_port, rtp_port, srv_ipt, srv_port);
    return 0;
}

static void on_sip(const uint8_t *buf, size_t n, const struct sockaddr_in *from)
{
    static struct msg m;                        /* 9 kB off the stack */
    if (n < 20 || parse(&m, buf, n)) return;
    long long now = now_ms();
    if (!m.req) {
        char branch[80] = "", cs[64] = ""; const char *v = hget(&m, "Via"), *cseq = hget(&m, "CSeq");
        param(v, "branch", branch, sizeof branch);
        if (cseq) sscanf(cseq, "%*u %63s", cs);
        if (reg.t.on && !strcmp(branch, reg.t.branch) && !strcmp(cs, "REGISTER")) on_register(&m, now);
        else if (!strcmp(cs, "INVITE") && c.outgoing && !strcmp(branch, c.inv.branch)) on_invite_response(&c, &m, now);
        else if (!strcmp(cs, "INVITE") && z.outgoing && !strcmp(branch, z.inv.branch)) on_invite_response(&z, &m, now);
        else if (bye_tx.on && !strcmp(branch, bye_tx.branch)) { if (m.code >= 200) bye_tx.on = 0; else bye_tx.answered = 1; }
        else if (cancel_tx.on && !strcmp(branch, cancel_tx.branch)) { if (m.code >= 200) cancel_tx.on = 0; else cancel_tx.answered = 1; }
        return;
    }
    /* requests: only the server reaches this port (its peer is fixed to it) */
    if (!strcmp(m.method, "INVITE")) on_invite(&m, from, now);
    else if (!strcmp(m.method, "ACK")) on_ack(&m);
    else if (!strcmp(m.method, "CANCEL")) on_cancel(&m, from);
    else if (!strcmp(m.method, "BYE")) on_bye(&m, from);
    else if (!strcmp(m.method, "OPTIONS")) reply(&m, from, 200, "OK", "Accept: application/sdp\r\n");
    else if (!strcmp(m.method, "NOTIFY")) reply(&m, from, 200, "OK", NULL);
    else reply(&m, from, 405, "Method Not Allowed", NULL);
}

static void on_sip_readable(void *ctx, int fd)
{
    static uint8_t buf[SIP_MAX + 1];
    (void)ctx;
    for (;;) {
        struct sockaddr_in from; socklen_t fl = sizeof from;
        ssize_t n = recvfrom(fd, buf, SIP_MAX, MSG_DONTWAIT, (struct sockaddr *)&from, &fl);
        if (n <= 0) break;
        on_sip(buf, (size_t)n, &from);
    }
}

/* ---------------------------------------------------------------- timers */

static void tx_tick(struct tx *t, long long now)
{
    if (!t->on) return;
    if (now >= t->until) { t->on = 0; return; }
    if (!t->answered && now >= t->next) { sip_send(t->buf, t->n); t->iv = t->iv * 2 < T2 ? t->iv * 2 : T2; t->next = now + t->iv; }
}

static void resp_tick(struct call *k, long long now)      /* a final response to an INVITE, until its ACK */
{
    if (!k->resp_until || k->acked) return;
    if (now >= k->resp_until) {
        k->resp_until = 0;
        if (k == &c && c.state == LIVE && !c.outgoing) { send_bye("no ACK"); end_call("no ACK for our answer", NULL, 1); }
        return;
    }
    if (now >= k->resp_next) { sip_send(k->resp, k->resp_n); k->resp_iv = k->resp_iv * 2 < T2 ? k->resp_iv * 2 : T2; k->resp_next = now + k->resp_iv; }
}

static void sip_tick(void)
{
    long long now = now_ms();
    if (!ready_done) return;
    if (!have_account()) {
        if (reg.line != LINE_OFF) { unregister(); set_line(LINE_OFF, cfg.server[0] ? "no valid extension entered" : "no phone system entered"); }
        if (reg.told < 0) { reg.told = LINE_OFF; hm_entity_state("line", "off"); }
        reg.t.on = 0; reg.next = 0;
        return;
    }
    if ((!srv_ip && now >= resolve_retry) || (srv_ip && now - srv_at > RESOLVE_MS)) resolve(now);
    if (!srv_ip || open_ports()) return;
    if (strcmp(reg_user, cfg.user) || strcmp(reg_pass, cfg.pass)) {          /* another account: register it */
        if (reg_user[0]) unregister();
        cpy(reg_user, sizeof reg_user, cfg.user); cpy(reg_pass, sizeof reg_pass, cfg.pass);
        reg.next = now; reg.t.on = 0; reg.granted = 0; reg.auth_tries = 0;
    }
    if (reg.line == LINE_OFF) set_line(LINE_CONNECTING, "registering at %s as %s", cfg.server, cfg.user);
    if (strcmp(local_ip, "0.0.0.0") && !reg.t.on && now >= reg.next) { reg.auth_tries = 0; reg_send(now, NULL, reg.granted > EXPIRES ? reg.granted : EXPIRES); }
    if (reg.t.on && now >= reg.t.until) {
        reg.t.on = 0; reg.next = now + RETRY_MS;
        set_line(LINE_UNREACHABLE, "no answer from %s", cfg.server);
    }
    tx_tick(&reg.t, now);
    /* the call */
    if (c.state == CALLING && c.outgoing) {
        tx_tick(&c.inv, now);
        if (!c.inv.on && !c.inv.answered && !c.ack_n && now >= c.inv.until) end_call("no answer from the server", "failed", 1);
        else if (now - c.since > RINGBACK_MS) { c.cancel = 1; c.cancel_sent = 1; cancel_send(&c); end_call("not answered", "not_answered", 1); }
    }
    if (c.state == LIVE && now - c.rx_at > LOST_MS) { send_bye("no audio"); end_call("lost: no audio for 2 min", NULL, 1); }
    resp_tick(&c, now);
    resp_tick(&z, now);
    if (z.outgoing && z.inv.on) tx_tick(&z.inv, now);
    if (z.call_id[0] && now - z.since > TX_MS + 1000) reset_call(&z);
    tx_tick(&bye_tx, now); tx_tick(&cancel_tx, now);
}

/* the helper thread: a byte every TIMER_MS, read by the loop (hm_watch).  It calls nothing of the SDK's */
static void *timer_thread(void *arg)
{
    int fd = (int)(intptr_t)arg;
    struct timespec ts = { 0, TIMER_MS * 1000000L };
    for (;;) {
        nanosleep(&ts, NULL);
        if (write(fd, "t", 1) < 0 && errno != EAGAIN) return NULL;
    }
}

static void on_timer(void *ctx, int fd)
{
    char b[64];
    (void)ctx;
    while (read(fd, b, sizeof b) > 0) {}
    sip_tick();
}

/* ---------------------------------------------------------------- the callbacks */

static void on_config(void *ctx, const struct hm_setting *s, int n)
{
    (void)ctx;
    for (int i = 0; i < n; i++) {
        const char *k = s[i].name, *v = s[i].value;
        if (!strcmp(k, "server")) cpy(cfg.server, sizeof cfg.server, v);
        else if (!strcmp(k, "port")) { int p = atoi(v); cfg.port = p > 0 && p < 65536 ? p : 5060; }
        else if (!strcmp(k, "user")) cpy(cfg.user, sizeof cfg.user, v);
        else if (!strcmp(k, "password")) cpy(cfg.pass, sizeof cfg.pass, v);
    }
    if (cfg.user[0] && !user_ok(cfg.user)) hm_log("the extension has a character a SIP user name cannot have: letters, digits and . _ + - * # only");
}

static void on_ready(void *ctx)
{
    int p[2]; pthread_t t;
    (void)ctx;
    rand_hex(reg.call_id, 12); strcat(reg.call_id, "@hassmic"); rand_hex(reg.tag, 8);
    if (pipe2(p, O_CLOEXEC | O_NONBLOCK) == 0 && pthread_create(&t, NULL, timer_thread, (void *)(intptr_t)p[1]) == 0) {
        pthread_detach(t); timer_fd = p[0]; hm_watch(timer_fd, on_timer);
    } else hm_log("no timer thread: SIP's retransmissions go by the second");
    ready_done = 1;
    hm_log("started; %s", have_account() ? "registers at its first tick" : "no phone system or extension entered yet");
}

static void on_tick(void *ctx)
{
    (void)ctx;
    static int told;
    sip_tick();                                 /* (its first round says the line's state, whatever it is) */
    if (reg.told < 0) { reg.told = reg.line; hm_entity_state("line", LINES[reg.line]); }
    if (!told) { told = 1; tell_state(); }
}

static void on_net(void *ctx, uint32_t address, uint32_t mask, int up)
{
    (void)ctx; (void)mask;
    if (up && address) srv_at = 0;              /* look again at once: the Echo may have a new address */
}

/* a person at the Echo answered the call that rings: our answer, and the call live */
static void on_call_answer(void *ctx)
{
    char body[1200];
    (void)ctx;
    if (c.state != RINGING) return;
    sdp(body, sizeof body, c.pt, c.codec);
    respond_invite(200, "OK", body);
    c.ok_sent = 1;
    go_live(now_ms());
}

/* hassmic ended the call: the button, "stop" or "hang up", the screen, a ring nobody answered, no CALL_LIVE in time */
static void on_call_hangup(void *ctx, int why)
{
    const char *w = why == HM_HANGUP_BUTTON ? "the action button" : why == HM_HANGUP_SPOKEN ? "said at the Echo" : why == HM_HANGUP_SCREEN ? "the screen"
                  : why == HM_HANGUP_NOT_ANSWERED ? "not answered" : why == HM_HANGUP_NOT_LIVE ? "not connected in time" : "the plugin stopped";
    (void)ctx;
    c.hassmic = 0;                              /* it knows: no CALL_END for it */
    switch (c.state) {
    case RINGING:
        if (why == HM_HANGUP_NOT_ANSWERED) respond_invite(480, "Temporarily Unavailable", NULL);
        else respond_invite(603, "Decline", NULL);
        end_call(w, NULL, 0);
        break;
    case LIVE: send_bye(w); end_call(w, NULL, 0); break;
    case CALLING:
        c.cancel = 1;
        if (c.inv.answered) { c.cancel_sent = 1; cancel_send(&c); }   /* else on the first provisional (on_invite_response) */
        end_call(w, "cancelled", 0);
        break;
    }
}

/* Home Assistant or the card: "call <number>" (it brings a call token), "hang_up" */
static void on_action(void *ctx, const char *name, int from, const struct hm_arg *args, int n)
{
    (void)ctx; (void)from;
    if (!strcmp(name, "hang_up")) {
        if (c.state == RINGING) { respond_invite(603, "Decline", NULL); end_call("hung up from Home Assistant", NULL, 1); }
        else if (c.state == LIVE) { send_bye("hung up from Home Assistant"); end_call("hung up from Home Assistant", NULL, 1); }
        else if (c.state == CALLING) {
            c.cancel = 1;
            if (c.inv.answered) { c.cancel_sent = 1; cancel_send(&c); }
            end_call("hung up from Home Assistant", "cancelled", 1);
        }
        return;
    }
    if (strcmp(name, "call")) return;
    char token[33], number[64] = "";
    cpy(token, sizeof token, hm_action_token());
    for (int i = 0; i < n; i++) if (!strcmp(args[i].name, "number")) cpy(number, sizeof number, args[i].value);
    if (!user_ok(number)) { hm_log("not a number to call: digits, letters, * # + . _ - only"); result("not_found"); return; }
    if (reg.line != LINE_UP || !srv_ip) { hm_log("not calling %s: %s", number, reg.text[0] ? reg.text : "not registered"); result("failed"); return; }
    if (c.state) { hm_log("not calling %s: already in a call with %s", number, c.peer); result("failed"); return; }
    int why = 0;
    if (hm_call_out(token, number, &why)) {
        hm_log("not calling %s: %s", number, why == HM_ERR_BUSY ? "the Echo is in a call" : why == HM_ERR_DENIED ? "the microphones are off, or the call's word was refused" : "refused by hassmic");
        result("failed");
        return;
    }
    reset_call(&c);
    c.outgoing = 1; c.state = CALLING; c.since = now_ms(); c.hassmic = 1;
    cpy(c.peer, sizeof c.peer, number);
    { char u[3 * sizeof cfg.user]; size_t k = 0;               /* '#' is not for a URI's user part as it is: %23 */
      for (const char *p = number; *p && k + 4 < sizeof u; p++) k += (size_t)(*p == '#' ? snprintf(u + k, sizeof u - k, "%%23") : snprintf(u + k, sizeof u - k, "%c", *p));
      struct out o = { c.ruri, 0, sizeof c.ruri }; put(&o, "sip:%s@%s", u, cfg.server); }
    rand_hex(c.call_id, 16); strcat(c.call_id, "@hassmic"); rand_hex(c.ltag, 8);
    { char nm[65]; cpy(nm, sizeof nm, hm_info()->name); clean(nm);
      snprintf(c.lhdr, sizeof c.lhdr, "\"%s\" <sip:%s@%s>;tag=%s", nm, cfg.user, cfg.server, c.ltag); }
    snprintf(c.ito0, sizeof c.ito0, "<%s>", c.ruri);
    cpy(c.rhdr, sizeof c.rhdr, c.ito0);
    hm_log("calling %s", number);
    tell_state();
    invite_send(c.since, NULL);
}

static void on_stop(void *ctx)
{
    (void)ctx;
    if (c.state == LIVE) send_bye("the plugin stopped");
    else if (c.state == RINGING) respond_invite(480, "Temporarily Unavailable", NULL);
    else if (c.state == CALLING && c.inv.answered) cancel_send(&c);
    unregister();
}

int main(void)
{
    static const struct hm_callbacks cb = { .config = on_config, .ready = on_ready, .tick = on_tick, .stop = on_stop, .net = on_net,
                                            .action = on_action, .call_answer = on_call_answer, .call_hangup = on_call_hangup,
                                            .call_audio = on_call_audio };
    return hm_run(&cb, NULL) ? 1 : 0;
}
