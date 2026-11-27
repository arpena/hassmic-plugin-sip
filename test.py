#!/usr/bin/env python3
"""The SIP plugin on the PC, in hassmic's own PC build: build/<id>-host, built for the PC as for an Echo (static: make
host), packed with this repository's package tool as a sideload (no listing: as "Install from file" takes it) and
installed with the action button into hassmic's build/hassmic-host under its build/plugrun-host.  This script is the
settings page, Home Assistant (keyed, as once adopted: its actions and the plugin's entities), and a stand-in phone
system written here from RFC 3261, 2617, 3550 and 3551, not from the plugin.

The phone system is another host behind a veth pair, in a network namespace of its own (hassmic's tests/peerhost.py,
made by its tests/run.py netns(peer=True)): 10.250.0.2, on the Echo's "LAN", SIP on 5060 and its RTP on 40000; a
stranger at 10.250.0.3.  Their sockets are made there and handed to this script (run.other_socket).  Run alone, the
script starts itself in such a namespace (unshare -rn), and removes the other host's socket and its folder at the end.

The install (its lines, the button); nothing until an account is entered; registration with the password by digest
(the challenge, qop=auth), the Echo's own address and port in the Contact, never the password itself.  Calls in: an
OPTIONS answered; a stranger's INVITE never reaches it; A-law rings (100, 180) and stays ringing until the action
button answers it at the Echo (200 with the answer, A-law); the far side's 1 kHz plays on the Echo's Voip stream
(HASSMIC_VOIP), the call microphone's 700 Hz (HASSMIC_CALL_CAP) goes back as A-law RTP, 20 ms a packet, from the
plugin's second port; a second call meanwhile is busy (486); the far side's BYE ends it.  mu-law answered as mu-law,
ended by "<wake word>, stop" (a BYE in the dialog).  No codec in common: 488.  Do not disturb: 480.  Cancelled while
it rings (200, 487, its ACK, nothing again); declined from Home Assistant (603); not answered (480); a fourth
unanswered ring in hassmic's window refused (480).  Calls out (Home Assistant's "call"): through a 407 (its ACK, the
INVITE again with the digest), ringing, answered (the 2xx ACKed), the Echo's audio to the answer's address, hung up
from Home Assistant (BYE); busy (486); cancelled while it rings (CANCEL, then the 487 ACKed); a number that is not
one.  Then a wrong password (line refused, no flood of tries), the right one again, and the plugin switched off
(unregistered: Expires 0).

Needs a hassmic tree (HASSMIC, default ../echo-dot-assist) with `make host` done and its .venv; run with that .venv's
python (make test).  The ports are fixed: one run at a time."""
import asyncio, base64, hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("HASSMIC") or os.path.join(HERE, "..", "echo-dot-assist"))
for f in ("hassmic-host", "plugrun-host"):
    if not os.path.exists(os.path.join(ROOT, "build", f)):
        sys.exit(f"no hassmic PC build in {ROOT} (build/{f}): set HASSMIC to a hassmic tree and run `make host` there")
sys.path.insert(0, os.path.join(HERE, "sdk"))
import plugcheck                                # this repository's copy: hassmic's helpers then use it too
sys.path.insert(0, os.path.join(ROOT, "tests"))
from webclient import wait_port, Browser
import fake_plugins as fp                       # hassmic's helpers for plugin tests (start, upload, post, until)
import run                                      # the other host: netns(peer=True), other_socket
check = fp.check
ID = json.load(open(os.path.join(HERE, "manifest.json")))["id"]
OBJ = f"p{len(ID)}_{ID}_"                       # hassmic's prefix of a plugin's entities and actions in Home Assistant
PBX, STRANGER, ECHO = "10.250.0.2", "10.250.0.3", "10.250.0.1"
SIP, MEDIA = 5060, 40000
USER, PASS, REALM = "70", "s3cret pass", "hassmic-test"


def reachable():
    """the other host is there (hassmic's tests/run.py netns(peer=True), tests/peerhost.py)"""
    return bool(os.environ.get("HASSMIC_PEER_SOCK")) and os.path.exists(os.environ["HASSMIC_PEER_SOCK"])


# ---------------------------------------------------------------- G.711 (Sun's g711.c, as ITU-T G.711), levels
import numpy as np


def _alaw(v):
    v >>= 3; mask = 0xd5
    if v < 0: v = -v - 1; mask = 0x55
    seg = next((i for i, e in enumerate((0x1f, 0x3f, 0x7f, 0xff, 0x1ff, 0x3ff, 0x7ff, 0xfff)) if v <= e), 8)
    if seg == 8: return 0x7f ^ mask
    return ((seg << 4) | ((v >> 1 if seg < 2 else v >> seg) & 0x0f)) ^ mask
def _alaw_dec(a):
    a ^= 0x55; t = (a & 0x0f) << 4; seg = (a & 0x70) >> 4
    t = t + 8 if seg == 0 else t + 0x108 if seg == 1 else (t + 0x108) << (seg - 1)
    return t if a & 0x80 else -t
def _ulaw(v):
    v >>= 2; mask = 0xff
    if v < 0: v = -v; mask = 0x7f
    v = min(v, 8159) + 33
    seg = next((i for i, e in enumerate((0x3f, 0x7f, 0xff, 0x1ff, 0x3ff, 0x7ff, 0xfff, 0x1fff)) if v <= e), 8)
    if seg == 8: return 0x7f ^ mask
    return ((seg << 4) | ((v >> (seg + 1)) & 0x0f)) ^ mask
def _ulaw_dec(u):
    u = ~u & 0xff; t = (((u & 0x0f) << 3) + 0x84) << ((u & 0x70) >> 4)
    return 0x84 - t if u & 0x80 else t - 0x84
ALAW = np.array([_alaw(v) for v in range(-32768, 32768)], np.uint8)
ULAW = np.array([_ulaw(v) for v in range(-32768, 32768)], np.uint8)
ALAW_DEC = np.array([_alaw_dec(a) for a in range(256)], np.int16)
ULAW_DEC = np.array([_ulaw_dec(u) for u in range(256)], np.int16)
encode = {8: lambda x: ALAW[x.astype(np.int32) + 32768].tobytes(), 0: lambda x: ULAW[x.astype(np.int32) + 32768].tobytes()}
decode = {8: lambda b: ALAW_DEC[np.frombuffer(b, np.uint8)], 0: lambda b: ULAW_DEC[np.frombuffer(b, np.uint8)]}


def db(x):
    x = np.asarray(x, float)
    return 10 * np.log10((x ** 2).mean() / 32768 ** 2 + 1e-20) if len(x) else -200
def tone_db(x, f, rate):
    x = np.asarray(x, float); t = np.arange(len(x)) / rate
    return 20 * np.log10(abs((x * np.exp(-2j * np.pi * f * t)).sum()) * 2 / max(len(x), 1) / np.sqrt(2) / 32768 + 1e-12)
def tone(f, secs, rate, dbfs=-20):
    return (32768 * 10 ** (dbfs / 20) * np.sqrt(2) * np.sin(2 * np.pi * f * np.arange(int(secs * rate)) / rate)).astype(np.int16)


# ---------------------------------------------------------------- SIP messages
class Msg:
    def __init__(self, data):
        text = data.decode(errors="replace")
        head, _, self.body = text.partition("\r\n\r\n")
        lines = head.split("\r\n")
        self.start = lines[0]; self.h = []
        for l in lines[1:]:
            k, _, v = l.partition(":"); self.h.append((k.strip(), v.strip()))
        p = self.start.split(" ", 2)
        self.req = not self.start.startswith("SIP/2.0")
        self.method, self.uri = (p[0], p[1]) if self.req else (None, None)
        self.code = None if self.req else int(p[1])
        self.raw = text
    def get(self, k, all=False):
        v = [b for a, b in self.h if a.lower() == k.lower()]
        return v if all else (v[0] if v else None)
    def cseq(self):
        n, m = self.get("CSeq").split(); return int(n), m
    def branch(self): return re.search(r"branch=([^;,\s]+)", self.get("Via")).group(1)
    def tag(self, k): m = re.search(r"tag=([^;,\s]+)", self.get(k) or ""); return m.group(1) if m else None


def digest_ok(auth, method, password=PASS):
    """an Authorization (or Proxy-) header as RFC 2617 has it, checked from our side"""
    f = dict(re.findall(r'(\w+)="?([^",]*)"?', auth[len("Digest"):]))
    if not {"username", "realm", "nonce", "uri", "response"} <= set(f): return False
    ha1 = hashlib.md5(f"{f['username']}:{f['realm']}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{f['uri']}".encode()).hexdigest()
    want = hashlib.md5(f"{ha1}:{f['nonce']}:{f['nc']}:{f['cnonce']}:{f['qop']}:{ha2}".encode() if f.get("qop") else f"{ha1}:{f['nonce']}:{ha2}".encode()).hexdigest()
    return f.get("response") == want and f.get("username") == USER and f.get("realm") == REALM


class Pbx(asyncio.DatagramProtocol):
    """the phone system: REGISTER answered by itself (a challenge, then the password checked), the rest queued"""
    def __init__(self):
        self.q, self.regs, self.password, self.refuse = [], [], PASS, False
        self.echo = None                            # where the Echo's SIP is (where its REGISTER came from)
    def connection_made(self, t): self.t = t
    def datagram_received(self, data, addr):
        m = Msg(data)
        if m.req and m.method == "REGISTER": self.on_register(m, addr); return
        self.q.append((m, addr))
    def respond(self, m, code, reason, addr, extra="", body="", to_tag=None):
        to = m.get("To") + (f";tag={to_tag}" if to_tag and "tag=" not in m.get("To") else "")
        hdr = "".join(f"Via: {v}\r\n" for v in m.get("Via", all=True))
        ct = "Content-Type: application/sdp\r\n" if body else ""
        self.t.sendto((f"SIP/2.0 {code} {reason}\r\n{hdr}From: {m.get('From')}\r\nTo: {to}\r\nCall-ID: {m.get('Call-ID')}\r\n"
                       f"CSeq: {m.get('CSeq')}\r\n{extra}{ct}Content-Length: {len(body)}\r\n\r\n{body}").encode(), addr)
    def on_register(self, m, addr):
        self.regs.append(m)
        auth = m.get("Authorization")
        if self.refuse and auth: self.respond(m, 403, "Forbidden", addr); return
        if not auth or not digest_ok(auth, "REGISTER", self.password):
            self.respond(m, 401, "Unauthorized", addr, f'WWW-Authenticate: Digest realm="{REALM}", nonce="{os.urandom(8).hex()}", qop="auth", algorithm=MD5\r\n')
            return
        self.echo = addr
        self.respond(m, 200, "OK", addr, f"Contact: <{re.search(r'<([^>]+)>', m.get('Contact')).group(1)}>;expires={m.get('Expires')}\r\n", to_tag="reg")
    def send(self, text, addr=None): self.t.sendto(text.encode(), addr or self.echo)
    async def expect(self, pred, secs=3):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            for i, (m, a) in enumerate(self.q):
                if pred(m): del self.q[i]; return m, a
            await asyncio.sleep(0.02)
        return None, None


class Media(asyncio.DatagramProtocol):
    """the phone system's RTP: what the Echo sends, kept as (payload type, payload, when)"""
    def __init__(self): self.got, self.src = [], None
    def connection_made(self, t): self.t = t
    def datagram_received(self, data, addr):
        if len(data) >= 12 and data[0] >> 6 == 2: self.got.append((data[1] & 0x7f, data[12:], time.monotonic())); self.src = addr
    async def stream(self, to, pt, frames):
        seq, ts, ssrc = int.from_bytes(os.urandom(2), "big"), int.from_bytes(os.urandom(4), "big"), os.urandom(4)
        t0 = time.monotonic()
        for i, f in enumerate(frames):
            hdr = bytes([0x80, pt | (0x80 if i == 0 else 0)]) + ((seq + i) & 0xffff).to_bytes(2, "big") + ((ts + i * 160) & 0xffffffff).to_bytes(4, "big") + ssrc
            self.t.sendto(hdr + f, to)
            await asyncio.sleep(max(0, t0 + (i + 1) * 0.02 - time.monotonic()))


def offer(codecs, port=MEDIA):
    """an SDP offer as a phone system makes it: codecs in its order, ("PCMA", 8) / ("PCMU", 0) / ("G722", 9)"""
    rtp = "".join(f"a=rtpmap:{pt} {n}/8000\r\n" for n, pt in codecs)
    return (f"v=0\r\no=pbx 1 1 IN IP4 {PBX}\r\ns=pbx\r\nc=IN IP4 {PBX}\r\nt=0 0\r\nm=audio {port} RTP/AVP "
            + " ".join(str(pt) for _, pt in codecs) + f"\r\n{rtp}a=ptime:20\r\na=sendrecv\r\n")


def sdp_media(body):
    m = re.search(r"m=audio (\d+) RTP/AVP ([\d ]+)", body); c = re.search(r"c=IN IP4 ([\d.]+)", body)
    return (c.group(1), int(m.group(1)), [int(x) for x in m.group(2).split()]) if m and c else None


class Call:
    """a call from the phone system to the Echo"""
    n = 0
    def __init__(self, pbx, display="Portero", user="60", codecs=(("PCMA", 8), ("PCMU", 0))):
        Call.n += 1
        self.pbx, self.cid, self.tag, self.branch = pbx, f"call{Call.n}-{os.urandom(4).hex()}@pbx", os.urandom(4).hex(), "z9hG4bK" + os.urandom(6).hex()
        self.frm = f'"{display}" <sip:{user}@{PBX}>;tag={self.tag}'
        h, p = pbx.echo
        self.ruri = f"sip:{USER}@{h}:{p}"
        self.to = f"<sip:{USER}@{PBX}>"; self.codecs, self.echo_tag = codecs, None
    def request(self, method, cseq=1, body="", branch=None, to=None):
        ct = "Content-Type: application/sdp\r\n" if body else ""
        return (f"{method} {self.ruri} SIP/2.0\r\nVia: SIP/2.0/UDP {PBX}:{SIP};rport;branch={branch or self.branch}\r\n"
                f"Max-Forwards: 70\r\nFrom: {self.frm}\r\nTo: {to or self.to}\r\nCall-ID: {self.cid}\r\nCSeq: {cseq} {method}\r\n"
                f"Contact: <sip:{self.frm.split('sip:')[1].split('@')[0]}@{PBX}:{SIP}>\r\n{ct}Content-Length: {len(body)}\r\n\r\n{body}")
    def invite(self, via=None):
        text = self.request("INVITE", body=offer(self.codecs))
        (via or self.pbx.t).sendto(text.encode(), self.pbx.echo)
    def mine(self, m): return m.get("Call-ID") == self.cid
    async def response(self, code, method="INVITE", secs=3):
        m, _ = await self.pbx.expect(lambda m: not m.req and self.mine(m) and m.code == code and m.cseq()[1] == method, secs)
        if m and m.tag("To"): self.echo_tag = m.tag("To")
        return m
    def ack(self, cseq=1, branch=None):
        self.pbx.send(self.request("ACK", cseq, branch=branch, to=f"{self.to};tag={self.echo_tag}"))
    def bye(self):
        self.pbx.send(self.request("BYE", 2, branch="z9hG4bK" + os.urandom(6).hex(), to=f"{self.to};tag={self.echo_tag}"))
    def cancel(self):
        self.pbx.send(self.request("CANCEL"))


async def until(cond, secs=8):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond(): return True
        await asyncio.sleep(0.05)
    return bool(cond())


def voip_tail(path, secs):
    """the last SECS of what the Echo's Voip stream played (16 kHz mono s16, hassmic's PC build: HASSMIC_VOIP)"""
    try: return np.fromfile(path, np.int16)[-16000 * secs:]
    except OSError: return np.zeros(0, np.int16)


async def scenario(top):
    from aioesphomeapi import APIClient, Event
    loop = asyncio.get_running_loop()
    _, pbx = await loop.create_datagram_endpoint(Pbx, sock=run.other_socket("udp", PBX, SIP))
    _, media = await loop.create_datagram_endpoint(Media, sock=run.other_socket("udp", PBX, MEDIA))
    stranger = run.other_socket("udp", STRANGER, SIP); stranger.setblocking(False)

    state = os.path.join(top, "state"); os.mkdir(state); log = os.path.join(top, "log")
    for k in ("author", "index"):              # (hassmic's helper names an index key's file: one that lists nothing here)
        plugcheck.otatool.keygen(os.path.join(top, k + ".key"), os.path.join(top, k + ".pub"))
    pkg = os.path.join(top, "pkg.hmp")
    plugcheck.pack(f"{HERE}/manifest.json", f"{HERE}/build/{ID}-host", os.path.join(top, "author.key"), pkg, license_=f"{HERE}/LICENSE", arch="x86_64")
    # community plugins on (a plugin from a file is not official); full scale (the call's audio as sent)
    open(os.path.join(state, "config"), "w").write("plugins=on\nplugins_community=on\neven_volume=off\n")
    api_key = base64.b64encode(os.urandom(32)).decode()
    open(os.path.join(state, "api_key"), "w").write(api_key + "\n")
    # the call microphone (HASSMIC_CALL_CAP): a 700 Hz tone at -20 dBFS, as Amazon's call pipeline leaves a talker
    callcap, voip = os.path.join(top, "callmic.raw"), os.path.join(state, "voip.raw")
    tone(700, 2, 16000).tofile(callcap)
    own = f"plugin {ID}: "
    text = lambda: open(log, errors="replace").read()
    plog = lambda: [l.split(own, 1)[1] for l in text().splitlines() if own in l]
    async def says(t, n0=0, secs=8): return await until(lambda: any(t in l for l in plog()[n0:]), secs)
    # rings of 6 s; the far side's answer within 10 s; a call out rings 20 s at most; rings counted over 20 s
    proc = fp.start(state, log, env={"HASSMIC_CALL_CAP": callcap, "HASSMIC_VOIP": voip,
                                             "HASSMIC_PLUGIN_CALL_TIMES": "6000:10000:20000:20000"})
    ha = None
    try:
        wait_port(fp.PORT, fp.WEB)
        b = Browser(fp.WEB)
        check(b.login_with_button(proc), "logged in with the button")

        # ---- the install: what the owner reads, then the action button
        st, r = fp.upload(b, open(pkg, "rb").read())
        lines = {c["cap"]: c["line"] for c in r.get("caps", [])}
        check(st == 200 and r.get("tier") == "sideload" and r.get("button") is True,
              f"the package checks, a sideload (no listing), waits for the button: {st} {r.get('tier')} {r.get('error')}")
        check(lines.get("call", "").startswith("Makes and takes calls: a call coming in is answered only at the Echo")
              and '"Call" (and have it place a call)' in lines.get("ha.actions", "") and "net.udp" in lines and "net.server" in lines,
              f"one line per capability, the call's and its action that places calls among them: {lines}")
        fp.post(b, "/api/plugins/install"); proc.send_signal(signal.SIGUSR2)      # (SIGUSR2: the action button)
        check(fp.until(lambda: (fp.plug(b, ID) or {}).get("accepted"), 15), "installed with the button")
        check(fp.until(lambda: (fp.plug(b, ID) or {}).get("state") == "running", 20), "it runs")
        check(await says("no phone system or extension entered yet"), f"nothing entered yet, in its log: {plog()[-2:]}")
        await asyncio.sleep(1.5)
        check(not pbx.regs, "nothing sent while no account is entered")

        # ---- the account: the server's rows (a restart), the extension, the password sealed
        for k, v in (("user", USER), ("port", SIP), ("server", PBX)):
            st, r = fp.post(b, f"/api/plugins/{ID}/set", f"{k}={v}")
            check(st == 200, f"the owner sets {k}: {st} {r}")
        def secret(pw):
            p = pw.encode(); p = p.ljust((len(p) // 16 + 1) * 16, b"\0")     # (sealed in blocks of 16, a NUL after it)
            st, _, body = b.call("POST", f"/api/plugins/{ID}/secret/password", lambda c: b.seal(c, p).hex().encode())
            return st, body
        st, body = secret(PASS)
        check(st == 200, f"the password, sealed for this Echo: {st} {body[:80]}")
        check(await until(lambda: pbx.echo is not None, 15), f"the Echo registers, answering the challenge ({len(pbx.regs)} REGISTERs)")
        if pbx.echo is None: return
        r = next(m for m in pbx.regs if m.get("Authorization"))
        check(digest_ok(r.get("Authorization"), "REGISTER") and "qop=auth" in r.get("Authorization"), "  its digest is right (RFC 2617, qop=auth)")
        check(pbx.echo[0] == ECHO and 28940 <= pbx.echo[1] <= 28999 and f"<sip:{USER}@{ECHO}:{pbx.echo[1]}>" in (r.get("Contact") or ""),
              f"  from the plugin's port, the Contact naming it: {pbx.echo} {r.get('Contact')}")
        check(PASS not in r.raw and PASS.encode().hex() not in r.raw, "  the password itself never goes out")
        check(await says(f"line registered: registered at {PBX} as {USER}"), f"  its log says so: {plog()[-2:]}")

        # ---- Home Assistant, keyed: the plugin's entities and actions
        states, seq = {}, []
        for _ in range(50):
            try:
                ha = APIClient("127.0.0.1", fp.PORT, None, noise_psk=api_key); await ha.connect(login=True); break
            except Exception: await asyncio.sleep(0.2)
        ents, svcs = await ha.list_entities_services()
        by, svc = {e.object_id: e for e in ents}, {s.name: s for s in svcs}
        key = {by[OBJ + n].key: n for n in ("phone", "phone_with", "line", "result") if OBJ + n in by}
        def on_state(s):
            v = getattr(s, "event_type", None) if isinstance(s, Event) else getattr(s, "state", None)
            n = key.get(s.key)
            if n: states[n] = v; seq.append((n, v))
        ha.subscribe_states(on_state)
        check(len(key) == 4 and {OBJ + "call", OBJ + "hang_up"} <= set(svc),
              f"Home Assistant: Phone, Phone with, Phone line, Phone result, and the actions call and hang_up: {sorted(by)} {sorted(svc)}")
        act = lambda name, **a: ha.execute_service(svc[OBJ + name], a)
        phone = lambda: (states.get("phone"), states.get("phone_with"))
        results = lambda: [v for n, v in seq if n == "result"]
        check(await until(lambda: states.get("line") == "registered" and phone() == ("idle", ""), 5), f"Phone line registered, Phone idle ({states})")

        # ---- requests: OPTIONS (a PBX's qualify); a stranger's INVITE never reaches the plugin (its peer is the server)
        c0 = Call(pbx)
        pbx.send(c0.request("OPTIONS"))
        check(await c0.response(200, "OPTIONS") is not None, "OPTIONS from the server: 200")
        cs = Call(pbx); cs.invite(via=stranger); await asyncio.sleep(1.5)
        try: got = stranger.recv(4000)
        except BlockingIOError: got = b""
        check(not got and phone()[0] == "idle", f"an INVITE from another address: no answer, nothing rings ({got[:40]!r})")

        # ---- 1. A-law: rings until answered at the Echo (the action button), the audio both ways
        c = Call(pbx); mark = len(seq); c.invite()
        check(await c.response(100) is not None and await c.response(180) is not None, "an INVITE: 100 Trying, 180 Ringing")
        check(await until(lambda: phone() == ("ringing", "Portero (60)"), 3), f"Phone ringing, Phone with \"Portero (60)\" ({phone()})")
        check(await until(lambda: f"{ID}: a call comes in: it rings" in text(), 2), "hassmic rings")
        await asyncio.sleep(1.5)
        check(phone()[0] == "ringing" and not [m for m, _ in pbx.q if c.mine(m) and m.code == 200], "  still ringing a while later: never answered by itself")
        proc.send_signal(signal.SIGUSR2)                                     # the action button
        ok = await c.response(200)
        sd = sdp_media(ok.body) if ok else None
        check(sd is not None and sd[0] == ECHO and sd[2] == [8] and "a=rtpmap:8 PCMA/8000" in ok.body,
              f"the action button answers: 200 OK, A-law (the offer's first), the Echo's address ({sd})")
        check(ok is not None and f"sip:{USER}@{ECHO}:{pbx.echo[1]}" in (ok.get("Contact") or ""), "  with the Echo's Contact")
        c.ack()
        check(await until(lambda: phone() == ("connected", "Portero (60)"), 3), f"Phone connected ({phone()})")
        check([v for n, v in seq[mark:] if n == "phone"] == ["ringing", "connected"], f"  Home Assistant hears ringing, connected ({[v for n, v in seq[mark:] if n == 'phone']})")
        to = (sd[0], sd[1])
        await media.stream(to, 8, [encode[8](f) for f in tone(1000, 3, 8000).reshape(-1, 160)])
        played = voip_tail(voip, 2)
        check(tone_db(played, 1000, 16000) > -24 and db(played) - tone_db(played, 1000, 16000) < 1,
              f"the Echo plays the far side's 1 kHz ({tone_db(played, 1000, 16000):.1f} dB of {db(played):.1f} dBFS; sent at -20)")
        media.got.clear()
        await media.stream(to, 8, [encode[8](np.zeros(160, np.int16))] * 150)     # the far side quiet: the room passes
        got = list(media.got)
        check(len(got) > 120 and all(pt == 8 and len(p) == 160 for pt, p, _ in got), f"the Echo sends A-law RTP, 20 ms a packet ({len(got)} packets in 3 s)")
        sent = np.concatenate([decode[8](p) for _, p, _ in got[-100:]]) if got else np.zeros(0)
        check(tone_db(sent, 700, 8000) > db(sent) - 1 and -26 < tone_db(sent, 700, 8000) < -14,
              f"  the call microphone's 700 Hz in it ({tone_db(sent, 700, 8000):.1f} dB of {db(sent):.1f} dBFS; -20 recorded)")
        check(media.src is not None and media.src[0] == ECHO and media.src[1] != pbx.echo[1] and 28940 <= media.src[1] <= 28999,
              f"  from the plugin's other port ({media.src})")
        c2 = Call(pbx, display="Desk", user="11"); c2.invite()
        check(await c2.response(486) is not None, "a second call meanwhile: 486 Busy Here")
        c2.ack()
        c.bye()
        check(await c.response(200, "BYE") is not None, "the far side hangs up: 200 to its BYE")
        check(await until(lambda: phone() == ("idle", ""), 3), f"Phone idle ({phone()})")
        check(await says("ended with Portero (60) after"), f"  its log says who hung up: {plog()[-1:]}")

        # ---- 2. mu-law only: answered as mu-law, ended by "<wake word>, stop"
        c = Call(pbx, codecs=(("G722", 9), ("PCMU", 0))); c.invite()
        check(await c.response(180) is not None and await until(lambda: phone()[0] == "ringing", 3), "a mu-law call rings")
        proc.send_signal(signal.SIGUSR2)
        ok = await c.response(200); sd = sdp_media(ok.body) if ok else None
        check(sd is not None and sd[2] == [0] and "PCMU/8000" in ok.body, f"answered: mu-law, the one of the offer it has ({sd})")
        c.ack(); to = (sd[0], sd[1]) if sd else (PBX, 0)
        media.got.clear()
        await media.stream(to, 0, [encode[0](f) for f in tone(1000, 2, 8000).reshape(-1, 160)])
        played = voip_tail(voip, 1)
        check(tone_db(played, 1000, 16000) > -24, f"the Echo plays the mu-law tone ({tone_db(played, 1000, 16000):.1f} dB)")
        check(len(media.got) > 60 and all(pt == 0 for pt, _, _ in media.got), f"and sends mu-law ({len(media.got)} packets)")
        proc.send_signal(signal.SIGHUP)                                      # "<wake word>, stop"
        bye, a = await pbx.expect(lambda m: m.req and m.method == "BYE" and c.mine(m))
        check(bye is not None and bye.tag("From") == c.echo_tag and bye.tag("To") == c.tag, "\"stop\" hangs up: a BYE in the dialog (its tags)")
        if bye: pbx.respond(bye, 200, "OK", a)
        check(await until(lambda: phone()[0] == "idle", 3), "Phone idle")

        # ---- 3. no codec in common; do not disturb
        c = Call(pbx, codecs=(("G722", 9),)); c.invite()
        check(await c.response(488) is not None, "no G.711 in the offer: 488 Not Acceptable Here")
        c.ack()
        b.set(do_not_disturb="on")
        c = Call(pbx); c.invite()
        check(await c.response(480) is not None and phone()[0] == "idle", "do not disturb: 480 Temporarily Unavailable, nothing rings")
        c.ack(); b.set(do_not_disturb="off")

        # ---- 4. rings that go unanswered: cancelled, declined from Home Assistant, not answered; then the fourth refused
        c = Call(pbx); c.invite()
        check(await c.response(180) is not None, "ringing again")
        c.cancel()
        check(await c.response(200, "CANCEL") is not None and await c.response(487) is not None, "the caller cancels: 200 to the CANCEL, 487 to the INVITE")
        c.ack()
        check(await until(lambda: phone()[0] == "idle", 3), "Phone idle")
        await asyncio.sleep(1.2)
        check(not [m for m, _ in pbx.q if c.mine(m) and m.code == 487], "  no 487 again after its ACK")
        c = Call(pbx); c.invite()
        check(await c.response(180) is not None, "ringing again")
        await act("hang_up")
        check(await c.response(603) is not None, "Home Assistant's hang_up while it rings: 603 Decline")
        c.ack()
        check(await c.response(603, secs=1.2) is None, "  not sent again after its ACK")
        c = Call(pbx); c.invite()
        check(await c.response(180) is not None, "ringing again")
        check(await c.response(480, secs=9) is not None, "not answered (hassmic's ring ends): 480 Temporarily Unavailable")
        c.ack()
        check(await until(lambda: phone()[0] == "idle", 3), "Phone idle")
        c = Call(pbx); c.invite()
        check(await c.response(480) is not None and await says("three rings unanswered"), f"a fourth unanswered ring: refused (480), hassmic's limit: {plog()[-1:]}")
        c.ack()

        # ---- 5. a call out: through the 407, ringing, answered, hung up from Home Assistant
        mark = len(seq)
        await act("call", number="60")
        inv, a = await pbx.expect(lambda m: m.req and m.method == "INVITE")
        check(inv is not None and inv.uri == f"sip:60@{PBX}", f"call 60: an INVITE to {inv.uri if inv else None}")
        if inv is None: return
        sd = sdp_media(inv.body)
        check(sd is not None and sd[0] == ECHO and sd[2] == [8, 0], f"  offering A-law and mu-law at the Echo's address ({sd})")
        check(await until(lambda: phone() == ("calling", "60"), 2), f"Phone calling 60 ({phone()})")
        pbx.respond(inv, 407, "Proxy Authentication Required", a, f'Proxy-Authenticate: Digest realm="{REALM}", nonce="{os.urandom(8).hex()}", algorithm=MD5\r\n', to_tag="t407")
        ack, _ = await pbx.expect(lambda m: m.req and m.method == "ACK")
        check(ack is not None and ack.cseq() == (inv.cseq()[0], "ACK") and ack.branch() == inv.branch() and ack.tag("To") == "t407",
              "  the 407 is ACKed in its transaction (same branch, CSeq, its To tag)")
        inv2, a = await pbx.expect(lambda m: m.req and m.method == "INVITE")
        check(inv2 is not None and inv2.cseq()[0] == inv.cseq()[0] + 1 and inv2.get("Call-ID") == inv.get("Call-ID"), "  then the INVITE again, CSeq + 1, the same call")
        if inv2 is None: return
        check(digest_ok(inv2.get("Proxy-Authorization") or "", "INVITE"), "  with Proxy-Authorization, the digest right (no qop)")
        pbx.respond(inv2, 100, "Trying", a)
        pbx.respond(inv2, 180, "Ringing", a, to_tag="callee")
        check(await until(lambda: results()[-1:] == ["ringing"], 3), f"Phone result: ringing ({results()[-1:]})")
        await asyncio.sleep(0.8)
        check(not [m for m, _ in pbx.q if m.req and m.method == "INVITE"], "  no INVITE again once it rings")
        pbx.respond(inv2, 200, "OK", a, f"Contact: <sip:60@{PBX}:{SIP}>\r\n", offer((("PCMA", 8),)), to_tag="callee")
        ack, _ = await pbx.expect(lambda m: m.req and m.method == "ACK")
        check(ack is not None and ack.cseq() == (inv2.cseq()[0], "ACK") and ack.tag("To") == "callee" and ack.branch() != inv2.branch(),
              "the 200 is ACKed (its own transaction, the callee's tag)")
        check(await until(lambda: phone() == ("connected", "60") and results()[-1:] == ["connected"], 3), f"Phone connected, result connected ({phone()}, {results()[-1:]})")
        media.got.clear(); await asyncio.sleep(1.5)
        check(len(media.got) > 50 and all(pt == 8 for pt, _, _ in media.got), f"  A-law RTP to the answer's address ({len(media.got)} packets in 1.5 s)")
        await act("hang_up")
        bye, a = await pbx.expect(lambda m: m.req and m.method == "BYE")
        check(bye is not None and bye.uri == f"sip:60@{PBX}:{SIP}" and bye.tag("To") == "callee", "Home Assistant's hang_up: a BYE to the callee's Contact, in the dialog")
        if bye: pbx.respond(bye, 200, "OK", a)
        check(await until(lambda: phone() == ("idle", ""), 3), "Phone idle")
        sts = [v for n, v in seq[mark:] if n == "phone"]
        check(sts == ["calling", "connected", "idle"], f"  states: {sts}")

        # ---- 6. busy; cancelled while it rings; not a number
        await act("call", number="61")
        inv, a = await pbx.expect(lambda m: m.req and m.method == "INVITE")
        if inv: pbx.respond(inv, 486, "Busy Here", a, to_tag="b")
        ack, _ = await pbx.expect(lambda m: m.req and m.method == "ACK")
        check(ack is not None and await until(lambda: results()[-1:] == ["busy"] and phone()[0] == "idle", 3), f"61 busy: ACKed, result busy ({results()[-1:]})")
        await act("call", number="62")
        inv, a = await pbx.expect(lambda m: m.req and m.method == "INVITE")
        if inv: pbx.respond(inv, 180, "Ringing", a, to_tag="r")
        check(await until(lambda: results()[-1:] == ["ringing"], 3), "62 rings")
        await act("hang_up")
        can, a2 = await pbx.expect(lambda m: m.req and m.method == "CANCEL")
        check(can is not None and can.branch() == inv.branch() and can.cseq() == (inv.cseq()[0], "CANCEL") and can.tag("To") is None,
              "hang_up while it rings there: a CANCEL of that INVITE (its branch, CSeq, no To tag)")
        if can: pbx.respond(can, 200, "OK", a2); pbx.respond(inv, 487, "Request Terminated", a, to_tag="r")
        ack, _ = await pbx.expect(lambda m: m.req and m.method == "ACK")
        check(ack is not None and await until(lambda: results()[-1:] == ["cancelled"] and phone()[0] == "idle", 3), f"  the 487 ACKed, result cancelled ({results()[-1:]})")
        await act("call", number="not a number")
        check(await until(lambda: results()[-1:] == ["not_found"], 3), f"a number with a space is not called: result {results()[-1:]}")

        # ---- 7. a wrong password: refused, and no flood of tries; the right one again
        n0 = len(pbx.regs); pbx.refuse = True
        secret("wrong")
        check(await until(lambda: states.get("line") == "refused", 8), f"a wrong password: Phone line refused ({states.get('line')})")
        await asyncio.sleep(2)
        check(len(pbx.regs) - n0 <= 3, f"  and no flood of tries ({len(pbx.regs) - n0} REGISTERs)")
        pbx.refuse = False
        n0 = len(pbx.regs); secret(PASS)
        check(await until(lambda: states.get("line") == "registered", 8), "the right one again: registered")

        # ---- 8. switched off: unregistered
        n0 = len(pbx.regs)
        fp.post(b, f"/api/plugins/{ID}/enable", "off")
        check(fp.until(lambda: (fp.plug(b, ID) or {}).get("state") == "stopped"), "switched off")
        check(await until(lambda: any(m.get("Expires") == "0" for m in pbx.regs[n0:]), 3), "  unregistered on its way out (Expires: 0)")
        check(not [l for l in plog() if l.startswith("send:")], "no send errors on the way")
        check(proc.poll() is None, "hassmic still runs")
    finally:
        if ha:
            try: await ha.disconnect()
            except Exception: pass
        proc.terminate()
        try: proc.wait(5)
        except subprocess.TimeoutExpired: proc.kill()
        leftovers(state)
        if check.failed:
            print("---- log"); print(text()[-8000:])


def leftovers(state):
    """nothing of this run stays behind: the launcher, a plugin, a hassmic that did not end (each has this run's
    HASSMIC_STATE in its environment)"""
    mark = f"HASSMIC_STATE={state}".encode()
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) == os.getpid(): continue
        try:
            if mark in open(f"/proc/{p}/environ", "rb").read().split(b"\0"): os.kill(int(p), signal.SIGKILL)
        except OSError: pass


def main():
    top = tempfile.mkdtemp()
    check.failed = False
    try: asyncio.run(scenario(top))
    finally: shutil.rmtree(top, True)
    print(f"{ID}: " + ("FAILED" if check.failed else "all passed"))
    sys.exit(1 if check.failed else 0)


if __name__ == "__main__":
    if not reachable():
        if os.environ.get("HASSMIC_NETNS_DONE"): sys.exit("test.py: no other host in this namespace (hassmic's tests/run.py netns(peer=True))")
        os.environ["HASSMIC_NETNS_DONE"] = "1"
        os.execvp("unshare", run.netns([sys.executable, os.path.abspath(__file__)] + sys.argv[1:], peer=True))
    try: main()
    finally:                                    # the other host's socket and its folder (run.netns made them)
        d = os.path.dirname(os.environ.get("HASSMIC_PEER_SOCK", "")) if reachable() else ""
        if d and os.path.basename(d).startswith("hassmic-peer."):
            for f in os.listdir(d): os.unlink(os.path.join(d, f))
            os.rmdir(d)
