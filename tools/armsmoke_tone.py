#!/usr/bin/env python3
"""The tone example's ARM program (build/tone, build/tone-rs) under qemu-arm user mode against a stand-in broker on
a socketpair, as tools/armsmoke.py does for the example: the 32-bit layouts (the stream's descriptor in SCM_RIGHTS,
poll for writing, write's EAGAIN on a full socket) and the start of a static musl program for ARMv7.  Not the
sandbox (qemu takes no guest filter): the run on an Echo is the proof.

    tools/armsmoke_tone.py <qemu-arm> <program> <name for the output>

The broker's side, written from sdk/PROTOCOL.md: HELLO -> WELCOME, CONFIG (1000 Hz at -20 dB) -> READY; from its
first tick MUSIC_OPEN (48 kHz, 2 channels) answered with one end of a stream socket; MUSIC_STATE playing; a second of
what it writes read and held to the tone (its pitch by zero crossings, its level); MUSIC_CMD pause -> MUSIC_STATE
paused and nothing more written; resume -> playing again; STOP: it ends, its stream with it.  When you change what
the tone does, change this script with it."""
import array, math, os, select, socket, struct, subprocess, sys, time

qemu, program, pid = sys.argv[1], sys.argv[2], sys.argv[3]
HELLO, WELCOME, CONFIG, READY, PING, PONG, STOP, OK, ERR = 1, 2, 4, 5, 6, 7, 8, 10, 11
MUSIC_OPEN, MUSIC_STATE, MUSIC_CMD = 70, 71, 74


def s(t): b = t if isinstance(t, bytes) else t.encode(); return struct.pack("<H", len(b)) + b
def msg(kind, mid, body=b""): return struct.pack("<HHI", kind, 0, mid) + body
# the manifest's requires (manifest.json / tone/manifest.json): what hassmic speaks with it, WELCOME's scopes
REQUIRES = [("core", 1), ("audio.music", 1)]


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


def main():
    ok = True
    def say(good, what):
        nonlocal ok
        print(("ok   " if good else "FAIL ") + f"{pid}: {what}"); ok = ok and bool(good)

    broker, plugin = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    fd = plugin.fileno()
    proc = subprocess.Popen(["sh", "-c", f'exec "$0" "$1" 3<&{fd} {fd}<&-', qemu, program], pass_fds=(fd,),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    plugin.close()
    broker.settimeout(10)
    states = []

    def get():
        d = broker.recv(32768)
        if not d: raise EOFError("the plugin closed its control socket")
        k, _, mid = struct.unpack_from("<HHI", d)
        return k, mid, d[8:]

    def until_state(want, secs=5):
        end = time.monotonic() + secs
        while time.monotonic() < end and select.select([broker], [], [], end - time.monotonic())[0]:
            k, mid, b = get()
            if k == MUSIC_STATE: states.append(b[0])
            if k == MUSIC_STATE and b[0] == want: return True
            if k == PING: broker.send(msg(PONG, mid))
        return False

    mine = theirs = None
    try:
        k, mid, b = get()
        offered = hello_scopes(b) if k == HELLO else None
        say(offered is not None and all(offered.get(n, 0) >= v for n, v in REQUIRES),
            f"HELLO first, offering every scope the manifest requires: {offered}")
        broker.send(msg(WELCOME, mid, welcome("Echo Dot 2", "echo-arm", "Echo", "")))
        cfg = [("frequency", "1000"), ("level", "-20")]
        broker.send(msg(CONFIG, 0, struct.pack("<H", len(cfg)) + b"".join(s(a) + s(v) for a, v in cfg)))
        k, mid, b = get()
        say(k == READY, f"READY after the first CONFIG (type {k})")
        k, mid, b = get()
        say(k == MUSIC_OPEN and struct.unpack("<IB", b[:5]) == (48000, 2), f"MUSIC_OPEN from its first tick: 48 kHz, 2 channels ({k}, {b.hex()})")
        mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        theirs.setblocking(False)
        broker.sendmsg([msg(OK, mid)], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [theirs.fileno()]))]); theirs.close()
        say(until_state(1), f"MUSIC_STATE playing: {states}")
        pcm, end = b"", time.monotonic() + 10
        mine.settimeout(1)
        while len(pcm) < 48000 * 4 and time.monotonic() < end:
            try: pcm += mine.recv(65536)
            except socket.timeout: pass
        left = struct.unpack(f"<{len(pcm) // 4 * 2}h", pcm[:len(pcm) // 4 * 4])[0::2]
        cross = sum(1 for a, c in zip(left, left[1:]) if a < 0 <= c)
        rms = math.sqrt(sum(v * v for v in left) / max(1, len(left))) / 32768
        say(len(left) >= 48000 and 990 <= cross <= 1010, f"a second of what it writes: 1000 Hz ({cross} rising zero crossings in {len(left)} frames)")
        say(abs(20 * math.log10(max(rms, 1e-9)) - (-23)) < 1, f"at -20 dB peak, a sine's RMS 3 dB under: {20 * math.log10(max(rms, 1e-9)):.1f} dBFS")
        broker.send(msg(MUSIC_CMD, 0, bytes([1, 1])))          # pause, the action button
        say(until_state(2), f"MUSIC_CMD pause: MUSIC_STATE paused: {states}")
        mine.settimeout(0.3); drained = 0
        try:
            while True:
                d = mine.recv(65536)
                if not d: break
                drained += len(d)
        except socket.timeout: pass
        time.sleep(0.5)
        try: more = len(mine.recv(65536))
        except (socket.timeout, BlockingIOError): more = 0
        say(more == 0, f"paused: nothing more written ({more} bytes after what was in the socket)")
        broker.send(msg(MUSIC_CMD, 0, bytes([2, 4])))          # resume, Home Assistant
        say(until_state(1), f"resume: playing again: {states}")
        broker.send(msg(STOP, 0))
        while select.select([broker], [], [], 3)[0]:
            try: get()
            except (EOFError, OSError): break
        rc = proc.wait(5)
        mine.settimeout(2); closed = False
        try:
            while True:
                if not mine.recv(65536): closed = True; break
        except (socket.timeout, OSError): pass
        say(closed and rc == 0, f"STOP: it ends, and its stream with it ({closed}, {rc})")
    except Exception as e:
        say(False, f"{type(e).__name__}: {e}")
    finally:
        if proc.poll() is None: proc.kill()
        if mine: mine.close()
        err = proc.stderr.read().decode(errors="replace")
        if not ok: print("---- its stderr\n" + err)
    print(f"{pid} on ARM: " + ("all passed" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


main()
