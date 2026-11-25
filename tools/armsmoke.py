#!/usr/bin/env python3
"""The plugin's ARM program (build/<id>) under qemu-arm user mode against a stand-in broker on a socketpair: what the
PC test cannot show, the 32-bit layouts (recvmsg's msghdr and the descriptor in SCM_RIGHTS, poll, send's flags,
sockaddr_in from getsockname and recvfrom), the timer thread, and the start of a static musl program for ARMv7.  Not
the sandbox: qemu takes no guest filter (hassmic's docs/plugins.md 5.8); plugcheck.py elf has looked at the file, and
the run on an Echo is the proof.

    tools/armsmoke.py <qemu-arm> <program> <name for the output>

The broker's side, written from sdk/PROTOCOL.md: HELLO -> WELCOME, CONFIG (a phone system, an extension, a password)
-> READY; the CONNECT that resolves the server answered with a UDP socket connected to a stand-in phone system here;
the two UDP_OPENs answered with sockets of this script's (UDP_OPENED); the SIP socket's UDP_PEER (connected here, as
hassmic does it); then on the phone system's side: a REGISTER with the Echo's address and port in its Contact, a 401
with a challenge, the REGISTER again with the right digest, a 200, and "registered" in the line entity; a PING
answered; STOP: the binding removed (Expires: 0), and the plugin ends with status 0."""
import array, hashlib, os, re, select, socket, struct, subprocess, sys, time

qemu, program, pid = sys.argv[1], sys.argv[2], sys.argv[3]
HELLO, WELCOME, CONFIG, READY, PING, PONG, STOP, OK, ERR = 1, 2, 4, 5, 6, 7, 8, 10, 11
CONNECT, UDP_OPEN, UDP_OPENED, UDP_PEER, ENTITY_STATE, EVENT = 60, 61, 62, 63, 90, 91
CAP_NET_SERVER, CAP_NET_UDP = 5, 8
# the manifest's requires (manifest.json): what hassmic speaks with it, WELCOME's scopes
REQUIRES = [("core", 1), ("net.server", 1), ("net.udp", 1), ("call", 1), ("ha.entities", 1), ("ha.actions", 1)]
SERVER, USER, PASS, REALM = "pbx.lan", "70", "arm pass", "armsmoke"


def s(t): b = t if isinstance(t, bytes) else t.encode(); return struct.pack("<H", len(b)) + b
def msg(kind, mid, body=b""): return struct.pack("<HHI", kind, 0, mid) + body
def rs(b, at): n, = struct.unpack_from("<H", b, at); return b[at + 2:at + 2 + n], at + 2 + n


def hello_scopes(b):
    """HELLO's body: the SDK's version and the scopes it offers, {name: version}; None when it is not that layout"""
    if len(b) < 6: return None
    n, = struct.unpack_from("<H", b, 4); at, out = 6, {}
    for _ in range(n):
        if at + 2 > len(b): return None
        k, = struct.unpack_from("<H", b, at)
        if at + 2 + k + 2 > len(b): return None
        out[b[at + 2:at + 2 + k].decode()] = struct.unpack_from("<H", b, at + 2 + k)[0]; at += 2 + k + 2
    return out if at == len(b) else None


def welcome():
    """WELCOME's body: clock_set, model, node, name, zone, then the scopes spoken (REQUIRES: also what was granted)"""
    return (struct.pack("<I", 1) + s("Echo Dot 3") + s("echo-arm") + s("Echo") + s("") + struct.pack("<H", len(REQUIRES))
            + b"".join(s(n) + struct.pack("<H", v) for n, v in REQUIRES))


def digest_ok(auth, method):
    f = dict(re.findall(r'(\w+)="?([^",]*)"?', auth[len("Digest"):]))
    if not {"username", "realm", "nonce", "uri", "response"} <= set(f): return False
    ha1 = hashlib.md5(f"{f['username']}:{f['realm']}:{PASS}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{f['uri']}".encode()).hexdigest()
    want = hashlib.md5(f"{ha1}:{f['nonce']}:{f['nc']}:{f['cnonce']}:{f['qop']}:{ha2}".encode() if f.get("qop") else f"{ha1}:{f['nonce']}:{ha2}".encode()).hexdigest()
    return f["response"] == want and f["username"] == USER and f["realm"] == REALM


def header(text, name):
    m = re.search(rf"^{name}:\s*(.*?)\r$", text, re.M | re.I)
    return m.group(1) if m else None


def respond(srv, req, addr, code, reason, extra=""):
    vias = "".join(f"Via: {v}\r\n" for v in re.findall(r"^Via:\s*(.*?)\r$", req, re.M))
    to = header(req, "To") + (";tag=arm" if code >= 200 and "tag=" not in header(req, "To") else "")
    srv.sendto((f"SIP/2.0 {code} {reason}\r\n{vias}From: {header(req, 'From')}\r\nTo: {to}\r\nCall-ID: {header(req, 'Call-ID')}\r\n"
                f"CSeq: {header(req, 'CSeq')}\r\n{extra}Content-Length: 0\r\n\r\n").encode(), addr)


def main():
    ok = True
    def say(good, what):
        nonlocal ok
        print(("ok   " if good else "FAIL ") + f"{pid}: {what}"); ok = ok and bool(good)

    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); srv.bind(("127.0.0.1", 0))
    broker, plugin = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    fd = plugin.fileno()                        # the plugin's end as its descriptor 3, as the launcher gives it
    proc = subprocess.Popen(["sh", "-c", f'exec "$0" "$1" 3<&{fd} {fd}<&-', qemu, program], pass_fds=(fd,),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    plugin.close()
    udp = {}                                    # handle: this script's copy of the socket the plugin was handed
    states = []                                 # (entity, value) as ENTITY_STATE said them

    def get(secs=10):
        if not select.select([broker], [], [], secs)[0]: raise TimeoutError("nothing from the plugin")
        d = broker.recv(32768)
        if not d: raise EOFError("the plugin closed its control socket")
        k, _, mid = struct.unpack_from("<HHI", d)
        return k, mid, d[8:]

    def hand(mid, kind, body, sock):
        broker.sendmsg([msg(kind, mid, body)], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [sock.fileno()]))])

    def serve(k, mid, b):
        """answer what the plugin asks as hassmic would: 1 when it was one of these"""
        if k == CONNECT:
            say(b == bytes([CAP_NET_SERVER, 0]) + s("") + b"\0\0", f"CONNECT: net.server, entry 0, the owner's host, its port setting: {b.hex()}")
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.connect(srv.getsockname())
            hand(mid, OK, b"", u); u.close()
            return 1
        if k == UDP_OPEN:
            kind, entry = b[0], b[1]
            say(kind == CAP_NET_UDP and entry in (0, 1) and entry not in udp, f"UDP_OPEN: net.udp, entry {entry}")
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.bind(("127.0.0.1", 0)); udp[entry] = u
            hand(mid, UDP_OPENED, struct.pack("<BH", entry, u.getsockname()[1]), u)
            return 1
        if k == UDP_PEER:
            h, addr, port = struct.unpack_from("<BIH", b)
            ip = socket.inet_ntoa(struct.pack(">I", addr))
            if h in udp: udp[h].connect((ip, port))
            say(h == 0 and (ip, port) == srv.getsockname(), f"UDP_PEER: the SIP socket's peer, the phone system: handle {h}, {ip}:{port}")
            broker.send(msg(OK, mid))
            return 1
        if k in (ENTITY_STATE, EVENT):
            n, at = rs(b, 0); v, _ = rs(b, at)
            states.append((n.decode(), v.decode()))
            if mid: broker.send(msg(OK, mid))
            return 1
        return 0

    def sip(secs=10):
        """the next SIP message at the phone system, the plugin's other messages served meanwhile"""
        end = time.monotonic() + secs
        while time.monotonic() < end:
            r = select.select([broker, srv], [], [], max(0, end - time.monotonic()))[0]
            if srv in r:
                d, a = srv.recvfrom(4096); return d.decode(errors="replace"), a
            if broker in r:
                k, mid, b = get()
                if not serve(k, mid, b): say(False, f"an unexpected message, type {k}")
        raise TimeoutError("no SIP message")

    try:
        k, mid, b = get()
        offered = hello_scopes(b) if k == HELLO else None
        say(offered is not None and all(offered.get(n, 0) >= v for n, v in REQUIRES), f"HELLO first, offering every scope the manifest requires: {offered}")
        broker.send(msg(WELCOME, mid, welcome()))
        cfg = [("server", SERVER), ("port", "5060"), ("user", USER), ("password", PASS)]
        broker.send(msg(CONFIG, 0, struct.pack("<H", len(cfg)) + b"".join(s(a) + s(v) for a, v in cfg)))
        k, mid, b = get()
        say(k == READY, f"READY after the first CONFIG (type {k})")
        req, addr = sip()
        sport = udp[0].getsockname()[1] if 0 in udp else 0
        say(req.startswith(f"REGISTER sip:{SERVER} SIP/2.0\r\n") and addr == udp[0].getsockname(), f"a REGISTER from the SIP socket: {req.splitlines()[0]!r} from {addr}")
        say(f"<sip:{USER}@127.0.0.1:{sport}>" in (header(req, "Contact") or ""), f"  its Contact: the address the server sees, the SIP port ({header(req, 'Contact')})")
        say(f"127.0.0.1:{sport};rport;branch=z9hG4bK" in (header(req, "Via") or ""), f"  its Via ({header(req, 'Via')})")
        respond(srv, req, addr, 401, "Unauthorized", f'WWW-Authenticate: Digest realm="{REALM}", nonce="{os.urandom(8).hex()}", qop="auth", algorithm=MD5\r\n')
        req, addr = sip()
        auth = header(req, "Authorization") or ""
        say(req.startswith("REGISTER ") and digest_ok(auth, "REGISTER"), f"the challenge answered: the digest right ({auth[:60]}...)")
        say(PASS not in req, "  the password itself never goes out")
        respond(srv, req, addr, 200, "OK", f"Contact: {header(req, 'Contact')};expires=600\r\n")
        end = time.monotonic() + 5
        while ("line", "registered") not in states and time.monotonic() < end:
            if select.select([broker], [], [], 0.2)[0]:
                k, mid, b = get()
                if not serve(k, mid, b): say(False, f"an unexpected message, type {k}")
        say(("line", "registered") in states, f"registered: the line entity says so ({states})")
        broker.send(msg(PING, 77))
        while True:
            k, mid, b = get()
            if serve(k, mid, b): continue
            say(k == PONG and mid == 77, f"PONG with the PING's id (type {k}, id {mid})"); break
        broker.send(msg(STOP, 0))
        req, addr = sip(5)
        say(req.startswith("REGISTER ") and header(req, "Expires") == "0", f"STOP: the binding removed (Expires: {header(req, 'Expires')})")
        rc = proc.wait(5)
        say(rc == 0, f"ends at STOP: {rc}")
    except Exception as e:
        say(False, f"{type(e).__name__}: {e}")
    finally:
        if proc.poll() is None: proc.kill()
        err = proc.stderr.read().decode(errors="replace")
        if not ok: print("---- its stderr\n" + err)
    print(f"{pid} on ARM: " + ("all passed" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


main()
