#!/usr/bin/env python3
"""The example plugin on the PC, in hassmic's own PC build: the C build (build/<id>-host) and the Rust one
(build/<id>-rs-host, id <id>-rs), each built for the PC as for an Echo (static: make host), packed with this
repository's package tool as a sideload (no listing: as "Install from file" takes it) and installed with the action
button into hassmic's build/hassmic-host under its build/plugrun-host.  This script is the settings page and the
server the plugin greets.

The server is another host behind a veth pair, in a network namespace of its own (hassmic's tests/peerhost.py, made
by its tests/run.py netns(peer=True)): 10.250.0.2, on the Echo's "LAN".  Its sockets are made there and handed to
this script (run.other_socket).  Run alone, the script starts itself in such a namespace (unshare -rn), and removes
the other host's socket and its folder at the end.

Each build: the install dialog's lines (storage, the server rows, the buttons), the button; it runs and sends nothing until a
server is entered; the settings reach it (the greeting's text, the interval, the protocol switched to TCP without a
restart); the action button held a second (SIGURG, as buttons.c tells hassmic) greets at once (the capability
`button`); the start counter in storage across the plugin's restarts and across hassmic's own; the stop callback
keeping the greeting count when the owner switches it off and when hassmic ends in order (SIGTERM), and not when
hassmic is killed (SIGKILL: the plugin is killed then, and goes on from the last count it kept).  The C build also: what hassmic does with more than 50 log lines a
minute (the "verbose" switch at one greeting a second; a minute or two).

The tone example (tone/, both builds: build/tone-host, build/tone-rs-host): its install line, the button; it plays on
the Echo's music path (hassmic's PC build writes what a music plugin plays into a file, HASSMIC_PLUG_MUSIC): a sine
at the frequency it is set to, followed when the owner changes it; the action button pauses it (nothing more plays)
and resumes it; switched off, it stops.

Needs a hassmic tree (HASSMIC, default ../echo-dot-assist) with `make host` done and its .venv; run with that .venv's
python (make test).  The ports are fixed: one run at a time.  When you change what the example does, change this
test with it: it is the example's, not hassmic's."""
import json, os, re, shutil, signal, socket, subprocess, sys, tempfile, threading, time

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
TONE = json.load(open(os.path.join(HERE, "tone", "manifest.json")))["id"]
LAN = "10.250.0.2"
PORT = 15555                                    # the port row, as the owner enters it
GREETING = re.compile(r"^(.*) from (\S+) \(start (\d+), greeting (\d+)\)$")


def reachable():
    """the other host is there (hassmic's tests/run.py netns(peer=True), tests/peerhost.py)"""
    return bool(os.environ.get("HASSMIC_PEER_SOCK")) and os.path.exists(os.environ["HASSMIC_PEER_SOCK"])


class Server:
    """the server the plugin greets: UDP datagrams and TCP lines, each kept as (protocol, text)"""
    def __init__(self, host):
        self.got, self.lk = [], threading.Lock()
        self.u = run.other_socket("udp", host, PORT)
        self.t = run.other_socket("tcp", host, PORT, 4)
        threading.Thread(target=self.udp, daemon=True).start()
        threading.Thread(target=self.accept, daemon=True).start()

    def udp(self):
        while True:
            d, _ = self.u.recvfrom(4096)
            with self.lk: self.got.append(("udp", d.decode(errors="replace")))

    def accept(self):
        while True:
            c, _ = self.t.accept()
            threading.Thread(target=self.lines, args=(c,), daemon=True).start()

    def lines(self, c):
        f = c.makefile("rb")
        for line in f:
            with self.lk: self.got.append(("tcp", line.decode(errors="replace").rstrip("\n")))

    def greetings(self, n0=0, how=None):
        """the greetings since the N0'th, as (protocol, text, node, start, number)"""
        with self.lk: got = self.got[n0:]
        out = []
        for k, t in got:
            m = GREETING.match(t)
            if m and (how is None or k == how): out.append((k, m.group(1), m.group(2), int(m.group(3)), int(m.group(4))))
        return out


def scenario(pid, program, manifest, srv, log_rate):
    top = tempfile.mkdtemp(); state = os.path.join(top, "state"); os.mkdir(state); log = os.path.join(top, "log")
    for k in ("author", "index"):              # (hassmic's helper names an index key's file: one that lists nothing here)
        plugcheck.otatool.keygen(os.path.join(top, k + ".key"), os.path.join(top, k + ".pub"))
    pkg = os.path.join(top, "pkg.hmp")
    plugcheck.pack(manifest, program, os.path.join(top, "author.key"), pkg, license_=f"{HERE}/LICENSE", arch="x86_64")
    # (community plugins on, as the owner switches them on an Echo's page, a setting as any since hassmic 00d492a: a
    # plugin from a file is not official, and since hassmic 3326cd4 none such is installed or runs while they are off)
    open(os.path.join(state, "config"), "w").write("plugins=on\nplugins_community=on\n")
    store = os.path.join(state, "plugins", pid, "storage")
    stored = lambda k: (open(os.path.join(store, k)).read() if os.path.exists(os.path.join(store, k)) else None)
    own = f"plugin {pid}: "
    plog = lambda: [l.split(own, 1)[1] for l in open(log, errors="replace").read().splitlines() if own in l]
    def says(text, n0=0, secs=15): return fp.until(lambda: any(text in l for l in plog()[n0:]), secs)
    state_of = lambda: (fp.plug(b, pid) or {}).get("state")
    def set_(k, v):
        st, r = fp.post(b, f"/api/plugins/{pid}/set", f"{k}={v}")
        check(st == 200, f"{pid}: the owner sets {k}: {st} {r}")
    proc = fp.start(state, log)
    try:
        wait_port(fp.PORT, fp.WEB)
        b = Browser(fp.WEB)
        check(b.login_with_button(proc), f"{pid}: logged in with the button")

        # ---- the install: what the owner reads, then the action button
        st, r = fp.upload(b, open(pkg, "rb").read())
        lines = [c["line"] for c in r.get("caps", [])]
        check(st == 200 and r.get("tier") == "sideload" and r.get("button") is True,
              f"{pid}: the package checks, a sideload (no listing), waits for the button: {st} {r.get('tier')} {r.get('error')}")
        check("Keeps up to 1 kB of its own data on this Echo" in lines
              and any('"Server" (UDP, the port you enter in "Port")' in l and '(TCP, the port you enter in "Port")' in l for l in lines)
              and "Hears presses of this Echo's buttons, as Home Assistant's \"Button\" does" in lines,
              f"{pid}: one line per capability, with its parameters: {lines}")
        fp.post(b, "/api/plugins/install"); proc.send_signal(signal.SIGUSR2)      # (SIGUSR2: the action button)
        installed = fp.until(lambda: (fp.plug(b, pid) or {}).get("accepted"), 15)
        check(installed, f"{pid}: installed with the button")
        if not installed: return
        check(fp.until(lambda: state_of() == "running", 20), f"{pid}: it runs")
        check(says("started 1 time, 0 greetings sent before; no server entered yet"), f"{pid}: its first start, in its log: {plog()[-2:]}")
        time.sleep(2)
        check(not srv.greetings(), f"{pid}: nothing sent while no server is entered")
        node = (re.search(r"^name: .* \(node (\S+)\)$", open(log).read(), re.M) or [None, "?"])[1]

        # ---- the settings: the server's rows restart it; the greeting and the interval reach it
        n0 = len(srv.got)
        set_("greeting", "hi there"); set_("interval", "1"); set_("port", PORT); set_("server", LAN)
        check(fp.until(lambda: len(srv.greetings(n0)) >= 3, 20), f"{pid}: greetings arrive at the server it was given, one a second: {srv.got[n0:][-3:]}")
        g = srv.greetings(n0)
        check(g and all(x[0] == "udp" and x[1] == "hi there" and x[2] == node for x in g),
              f"{pid}: over UDP, with the owner's greeting and the Echo's node ({node}): {g[:2]}")
        starts = g[-1][3] if g else 0
        check(starts >= 2 and stored("starts") == str(starts),
              f"{pid}: started again when the server's rows changed, the start counted in storage: {starts}, stored {stored('starts')}")
        check(says("connected to the server (UDP)"), f"{pid}: says once that it connected")

        # ---- the protocol: not a row of net.server's, so no restart; the plugin follows it itself
        pids = fp.plugin_pids(state)
        n0 = len(srv.got)
        set_("protocol", "TCP")
        check(fp.until(lambda: len(srv.greetings(n0, "tcp")) >= 2, 15), f"{pid}: switched to TCP: lines arrive on a connection")
        t = srv.greetings(n0, "tcp")
        check(fp.plugin_pids(state) == pids and t and t[-1][3] == starts, f"{pid}: the same process, the same start ({starts})")

        # ---- the action button held a second (`button`): a greeting at once.  An hour between greetings first (a
        # changed interval greets at once too: that one is waited out), then the hold
        set_("interval", "3600"); time.sleep(2.5)
        n0 = len(srv.got); time.sleep(2)
        check(not srv.greetings(n0), f"{pid}: an hour between greetings: none for two seconds")
        l0 = len(plog()); proc.send_signal(signal.SIGURG)
        check(fp.until(lambda: srv.greetings(n0), 5) and says("the action button held: greeting now", l0),
              f"{pid}: the action button held a second: a greeting at once, and its log says why: {srv.got[n0:]} {plog()[l0:]}")
        set_("interval", "1")

        # ---- switched off: the stop callback keeps the greeting count
        fp.post(b, f"/api/plugins/{pid}/enable", "off")
        check(fp.until(lambda: state_of() == "stopped"), f"{pid}: switched off")
        time.sleep(0.5)
        last = max(x[4] for x in srv.greetings())
        check(stored("sent") == str(last), f"{pid}: its stop kept the count: stored {stored('sent')}, the server's last greeting {last}")
        n0 = len(srv.got)
        fp.post(b, f"/api/plugins/{pid}/enable", "on")
        check(fp.until(lambda: srv.greetings(n0), 20), f"{pid}: switched on again: it greets")
        g = srv.greetings(n0)
        check(g and g[0][3] == starts + 1 and g[0][4] == last + 1 and g[0][0] == "tcp",
              f"{pid}: the next start ({starts + 1}), the count going on from what was kept ({last + 1}), still TCP: {g[:1]}")

        # ---- hassmic ends in order (SIGTERM, which the Echo's own restarts send first): STOP, and the stop keeps the count
        time.sleep(1.5)
        proc.terminate(); proc.wait(10)
        check(fp.until(lambda: not fp.plugin_pids(state), 10), f"{pid}: hassmic ended, and the plugin with it")
        last = max(x[4] for x in srv.greetings())
        check(stored("sent") == str(last), f"{pid}: hassmic ended by SIGTERM: its stop kept the count: stored {stored('sent')}, the server's last greeting {last}")
        n0 = len(srv.got)
        proc = fp.start(state, log)
        wait_port(fp.PORT, fp.WEB)
        check(fp.until(lambda: srv.greetings(n0), 30), f"{pid}: hassmic started again: so is the plugin")
        g = srv.greetings(n0)
        check(g and g[0][3] == starts + 2 and g[0][4] == last + 1,
              f"{pid}: its start counted across hassmic's restart ({starts + 2}), the count from storage ({last + 1}): {g[:1]}")

        # ---- hassmic killed (SIGKILL: as Android's own stop of the service, a crash, a power cut): no stop, and the
        # plugin goes on from what storage has
        check(fp.until(lambda: max(x[4] for x in srv.greetings()) >= last + 2, 10), f"{pid}: (greets on)")
        kept = int(stored("sent") or 0)
        proc.kill(); proc.wait(10)
        check(fp.until(lambda: not fp.plugin_pids(state), 10), f"{pid}: hassmic killed, and the plugin with it (the launcher ends it)")
        sent = max(x[4] for x in srv.greetings())
        check(int(stored("sent") or 0) == kept and kept < sent, f"{pid}: no stop callback then: the count as it was last kept ({kept}), not the last greeting ({sent})")
        n0 = len(srv.got)
        proc = fp.start(state, log)
        wait_port(fp.PORT, fp.WEB)
        check(fp.until(lambda: srv.greetings(n0), 30), f"{pid}: hassmic started again: so is the plugin")
        g = srv.greetings(n0)
        check(g and g[0][3] == starts + 3 and g[0][4] == kept + 1,
              f"{pid}: its start counted ({starts + 3}), the count from storage ({kept + 1}): {g[:1]}")

        # ---- hassmic's log keeps 50 lines a minute of a plugin and counts the rest
        if log_rate:
            n0, g0 = len(plog()), len(srv.got)
            set_("verbose", "1")                # one line a greeting, a greeting a second: 60 a minute
            said = re.compile(r"^\((\d+) more lines in that minute, not kept\)$")
            check(fp.until(lambda: any(said.match(l) for l in plog()[n0:]), 150),
                  f"{pid}: past 50 lines in a minute hassmic keeps none, and says how many it dropped: {plog()[-1:]}")
            kept_lines = sum(1 for l in plog()[n0:] if l.startswith("greeted the server: "))
            sent = len(srv.greetings(g0))
            check(kept_lines < sent, f"{pid}: {sent} greetings sent, {kept_lines} of the lines saying so in the log")
            set_("verbose", "0")
        check(proc.poll() is None, f"{pid}: hassmic still runs")
    finally:
        proc.terminate()
        try: proc.wait(5)
        except subprocess.TimeoutExpired: proc.kill()
        leftovers(state)
    if check.failed:
        print("---- log"); print(open(log, errors="replace").read()[-6000:])
    shutil.rmtree(top)                          # (this run's folder by its own path: its state, log and keys)


def pitch(path, secs=1.0, rate=48000):
    """the frequency of the last SECS of what the plugin stream played (stereo s16 at RATE), by rising zero crossings"""
    import struct
    want = int(secs * rate) * 4
    with open(path, "rb") as f:
        f.seek(0, 2); size = f.tell(); f.seek(max(0, size - want) // 4 * 4); d = f.read()
    left = struct.unpack(f"<{len(d) // 4 * 2}h", d[:len(d) // 4 * 4])[0::2]
    return sum(1 for a, c in zip(left, left[1:]) if a < 0 <= c) / max(1e-9, len(left) / rate)


def tone_scenario(pid, program, manifest):
    """the tone example: a music source (audio.music)"""
    top = tempfile.mkdtemp(); state = os.path.join(top, "state"); os.mkdir(state); log = os.path.join(top, "log")
    for k in ("author", "index"):
        plugcheck.otatool.keygen(os.path.join(top, k + ".key"), os.path.join(top, k + ".pub"))
    pkg = os.path.join(top, "pkg.hmp"); music = os.path.join(state, "plug.raw")
    plugcheck.pack(manifest, program, os.path.join(top, "author.key"), pkg, license_=f"{HERE}/LICENSE", arch="x86_64")
    # (community plugins on, as the owner switches them on an Echo's page, a setting as any since hassmic 00d492a: a
    # plugin from a file is not official, and since hassmic 3326cd4 none such is installed or runs while they are off)
    open(os.path.join(state, "config"), "w").write("plugins=on\nplugins_community=on\n")
    own = f"plugin {pid}: "
    plog = lambda: [l.split(own, 1)[1] for l in open(log, errors="replace").read().splitlines() if own in l]
    def says(text, n0=0, secs=15): return fp.until(lambda: any(text in l for l in plog()[n0:]), secs)
    grows = lambda: (lambda n: fp.until(lambda: os.path.exists(music) and os.path.getsize(music) > n + 48000, 3))(os.path.getsize(music) if os.path.exists(music) else 0)
    proc = fp.start(state, log, env={"HASSMIC_PLUG_MUSIC": music})
    try:
        wait_port(fp.PORT, fp.WEB)
        b = Browser(fp.WEB)
        check(b.login_with_button(proc), f"{pid}: logged in with the button")
        st, r = fp.upload(b, open(pkg, "rb").read())
        lines = [c["line"] for c in r.get("caps", [])]
        check(st == 200 and r.get("button") is True and lines == ["Plays sound on this Echo's speaker, whenever it likes"],
              f"{pid}: one line, audio.music's, and the button: {st} {lines} {r.get('error')}")
        fp.post(b, "/api/plugins/install"); proc.send_signal(signal.SIGUSR2)
        check(fp.until(lambda: (fp.plug(b, pid) or {}).get("state") == "running", 20), f"{pid}: installed with the button, it runs")
        check(says("a tone of 440 Hz at -20 dB") and says("playing"), f"{pid}: its settings, then playing: {plog()[-3:]}")
        check(fp.until(lambda: os.path.exists(music) and os.path.getsize(music) > 4 * 48000, 10), f"{pid}: what it writes plays (hassmic's plugin stream)")
        f = pitch(music)
        check(430 < f < 450, f"{pid}: a sine of 440 Hz: {f:.0f} Hz")
        check((fp.plug(b, pid) or {}).get("music") == "playing", f"{pid}: its card says it plays")
        st, r = fp.post(b, f"/api/plugins/{pid}/set", "frequency=1000")
        check(st == 200 and says("a tone of 1000 Hz at -20 dB"), f"{pid}: the owner changes the frequency: it reaches it")
        time.sleep(1.5)
        f = pitch(music)
        check(980 < f < 1020, f"{pid}: and it plays it: {f:.0f} Hz")
        # the action button: pause, and nothing more plays; again: playing
        n0 = len(plog()); proc.send_signal(signal.SIGUSR2)
        check(says("asked to pause by the action button", n0) and says("paused", n0), f"{pid}: the action button pauses it: {plog()[n0:]}")
        time.sleep(0.5); n = os.path.getsize(music); time.sleep(1)
        check(os.path.getsize(music) - n < 4800, f"{pid}: nothing more plays while it is paused")
        n0 = len(plog()); proc.send_signal(signal.SIGUSR2)
        check(says("asked to resume by the action button", n0) and says("playing", n0) and grows(), f"{pid}: pressed again: it plays again")
        # switched off: it stops, its stream ends (hassmic closes the plugin stream: "audio: plugin stream closed"), and
        # nothing more is written.  (The check looked for the stream's start, which the log had from the first: it held
        # whatever came after; a review of hassmic efde36e, M4)
        l0 = len(open(log, errors="replace").read())
        fp.post(b, f"/api/plugins/{pid}/enable", "off")
        check(fp.until(lambda: (fp.plug(b, pid) or {}).get("state") == "stopped") and fp.logged(log, "audio: plugin stream closed", l0), f"{pid}: switched off, it stops and its stream ends")
        time.sleep(0.5); n = os.path.getsize(music); time.sleep(1)
        check(os.path.getsize(music) == n, f"{pid}: ... and nothing more plays ({os.path.getsize(music) - n} bytes)")
    finally:
        proc.terminate()
        try: proc.wait(5)
        except subprocess.TimeoutExpired: proc.kill()
        leftovers(state)
    if check.failed:
        print("---- log"); print(open(log, errors="replace").read()[-6000:])
    shutil.rmtree(top)


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
    srv = Server(LAN)
    builds = [(ID, f"{HERE}/build/{ID}-host", f"{HERE}/manifest.json", True),
              (f"{ID}-rs", f"{HERE}/build/{ID}-rs-host", f"{HERE}/build/{ID}-rs.json", False)]
    only = sys.argv[1:]
    failed = False
    for pid, program, manifest, log_rate in builds:
        if only and pid not in only: continue
        check.failed = False
        with srv.lk: srv.got.clear()
        scenario(pid, program, manifest, srv, log_rate)
        print(f"{pid}: " + ("FAILED" if check.failed else "all passed"))
        failed = failed or check.failed
    for pid, program, manifest in ((TONE, f"{HERE}/build/{TONE}-host", f"{HERE}/tone/manifest.json"),
                                   (f"{TONE}-rs", f"{HERE}/build/{TONE}-rs-host", f"{HERE}/build/{TONE}-rs.json")):
        if only and pid not in only: continue
        check.failed = False
        tone_scenario(pid, program, manifest)
        print(f"{pid}: " + ("FAILED" if check.failed else "all passed"))
        failed = failed or check.failed
    sys.exit(1 if failed else 0)


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
