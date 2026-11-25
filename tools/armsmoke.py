#!/usr/bin/env python3
"""An ARM program of the plugin (build/<id>, build/<id>-rs) under qemu-arm user mode against a stand-in broker on a
socketpair: what the PC test cannot show, the 32-bit layouts (recvmsg's msghdr and the descriptor in SCM_RIGHTS,
poll, send's flags) and the start of a static musl program for ARMv7.  Not the sandbox: qemu takes no guest filter
(hassmic's docs/plugins.md 5.8); plugcheck.py elf has looked at the file, and the run on an Echo is the proof.

    tools/armsmoke.py <qemu-arm> <program> <name for the output>

The broker's side, written from sdk/PROTOCOL.md: HELLO -> WELCOME (node "echo-arm"), CONFIG (a server, UDP, every 1 s)
-> the start counter read and written in ready (STORE_GET "starts" answered NOT_FOUND, "sent" answered 41, STORE_PUT
"starts" = 1) -> READY; a PING answered; the CONNECT it makes from its tick answered with a UDP socket connected to a
server here; a greeting there; then STOP, and the greeting count kept (STORE_PUT "sent") before it ends.  When you
change what the example does, change this script with it: it is the example's, not hassmic's."""
import array, os, select, socket, struct, subprocess, sys

qemu, program, pid = sys.argv[1], sys.argv[2], sys.argv[3]
HELLO, WELCOME, CONFIG, READY, PING, PONG, STOP, OK, ERR = 1, 2, 4, 5, 6, 7, 8, 10, 11
STORE_GET, STORE_VALUE, STORE_PUT, CONNECT = 40, 41, 42, 60
ERR_NOT_FOUND = 6


def s(t): b = t if isinstance(t, bytes) else t.encode(); return struct.pack("<H", len(b)) + b
def msg(kind, mid, body=b""): return struct.pack("<HHI", kind, 0, mid) + body
# the manifest's requires (manifest.json / tone/manifest.json): what hassmic speaks with it, WELCOME's scopes
REQUIRES = [("core", 1), ("storage", 1), ("button", 1), ("net.server", 1)]


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


def welcome(model, node, name, zone):
    """WELCOME's body: clock_set, model, node, name, zone, then the scopes spoken (REQUIRES: also what was granted)"""
    return (struct.pack("<I", 1) + s(model) + s(node) + s(name) + s(zone) + struct.pack("<H", len(REQUIRES))
            + b"".join(s(n) + struct.pack("<H", v) for n, v in REQUIRES))
def rs(b, at): n, = struct.unpack_from("<H", b, at); return b[at + 2:at + 2 + n], at + 2 + n


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
    broker.settimeout(10)
    stored = {"sent": b"41"}
    puts = []

    def get():
        d = broker.recv(32768)
        if not d: raise EOFError("the plugin closed its control socket")
        k, _, mid = struct.unpack_from("<HHI", d)
        return k, mid, d[8:]

    def storage(k, mid, b):
        """answer a storage request as hassmic does: 1 when it was one"""
        if k == STORE_GET:
            key, _ = rs(b, 0)
            v = stored.get(key.decode())
            broker.send(msg(STORE_VALUE, mid, s(v)) if v is not None else msg(ERR, mid, struct.pack("<H", ERR_NOT_FOUND)))
            return 1
        if k == STORE_PUT:
            key, at = rs(b, 0); v, _ = rs(b, at)
            stored[key.decode()] = v; puts.append((key.decode(), v))
            broker.send(msg(OK, mid))
            return 1
        return 0

    try:
        k, mid, b = get()
        offered = hello_scopes(b) if k == HELLO else None
        say(offered is not None and all(offered.get(n, 0) >= v for n, v in REQUIRES),
            f"HELLO first, offering every scope the manifest requires: {offered}")
        broker.send(msg(WELCOME, mid, welcome("Echo Dot 2", "echo-arm", "Echo", "")))
        cfg = [("greeting", "hi"), ("interval", "1"), ("protocol", "UDP"), ("verbose", "0"), ("server", "srv"), ("port", "514")]
        broker.send(msg(CONFIG, 0, struct.pack("<H", len(cfg)) + b"".join(s(a) + s(v) for a, v in cfg)))
        while True:
            k, mid, b = get()
            if storage(k, mid, b): continue
            break
        say(k == READY, f"READY after the first CONFIG (type {k})")
        say(puts == [("starts", b"1")], f"its start counted in ready: {puts}")
        # its first tick comes at once after READY: the CONNECT and the PONG in either order
        broker.send(msg(PING, 77))
        seen = set()
        while len(seen) < 2:
            k, mid, b = get()
            if k == PONG:
                say(mid == 77, "PONG with the PING's id"); seen.add("pong")
            elif k == CONNECT:
                say(b == b"\5\0\0\0\0\0", f"CONNECT from its tick: kind 5 (net.server), entry 0 (UDP), host empty, port 0: {b.hex()}")
                u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.connect(srv.getsockname())
                broker.sendmsg([msg(OK, mid)], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [u.fileno()]))]); u.close()
                seen.add("connect")
            elif not storage(k, mid, b):
                say(False, f"an unexpected message, type {k}"); break
        srv.settimeout(10)
        got = srv.recv(4096)
        say(got == b"hi from echo-arm (start 1, greeting 42)", f"a greeting through the descriptor it was handed: {got!r}")
        broker.send(msg(STOP, 0))
        puts.clear()
        while select.select([broker], [], [], 3)[0]:
            try: k, mid, b = get()
            except (EOFError, OSError): break
            storage(k, mid, b)
        rc = proc.wait(5)
        n = int(stored["sent"])
        say(puts and puts[-1][0] == "sent" and n >= 42, f"the greeting count kept at STOP: {puts}")
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
