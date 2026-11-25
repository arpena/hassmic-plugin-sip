//! A client of hassmic's plugin protocol, written from sdk/PROTOCOL.md alone: the framing, the start, PING, CONFIG,
//! STOP, TIME, storage, the log, CONNECT, music (audio.music: the tone example, src/bin/tone.rs) and the buttons; the
//! messages of files, wake, calls and the rest are not these plugins' and are answered as any type it does not know.
//! Versions (PROTOCOL.md "Versions"): it offers hassmic the scopes it implements, each at version 1 (SCOPES), in
//! HELLO, keeps to what WELCOME says is spoken (the manifest's "requires", which is also what was granted), and
//! takes nothing of a scope not spoken; a packet is exactly its layout: bytes after its last field make it malformed,
//! and so does a value of an enum this client does not speak (every scope at version 1: what version 1 of its scope
//! has; PROTOCOL.md "Values, constants and keys"), which none of its writers writes either.  One thread, one loop over the control socket (descriptor 3), as the C
//! client: a request that waits for its answer answers PING meanwhile and keeps what else arrives for afterwards, in
//! order.
//!
//! The same rules as the C client of hassmic's sdk/hmplugin.c (client version 4, after a review of hassmic 0dabfa6):
//! - what arrives while a request waits is kept, in order, up to KEPT_BYTES and KEPT_MAX; past that the plugin ends
//!   (exit 70) and says why, never drops a message silently (a LOG dropped was lines lost, a CONFIG or STOP unread);
//!   the loop hands on what was kept before anything newer (it read up to 64 new ones first: R3);
//! - every LOG is answered OK with its id once `log` returned: hassmic sends the next one after that OK (flow control,
//!   PROTOCOL.md "The log"; R9), so a slow `log` holds the log back on the Echo and fills nothing here;
//! - `log_follow` puts aside the LOGs that came before its answer, the kept ones and those that come while it waits:
//!   dropped once hassmic took the new place (they are the old place's: its log pump and its handling of LOG_FOLLOW
//!   are one thread), handed on as before when it refused it (R8);
//! - a STOP that comes while a request waits ends the wait, but a storage write's (hassmic answers those until the
//!   plugin is gone, anything else FAILED): the request fails with ERR_FAILED and `stop` runs as soon as the callback
//!   returns (R2); from then on requests but storage writes fail at once, a write goes now or not at all, and what
//!   was kept for later is written as `run` ends (R4);
//! - CONNECT only once READY went out (hassmic answers BAD before it), so from `tick` or a callback after `ready`;
//! - after a tick the loop reads what waits, up to 64 messages, before the next tick: PING never waits behind ticks;
//! - a storage write the Echo is BUSY for is kept (the latest per key, 8 keys) and written later; with no room for it
//!   `store_put` says BUSY, and one the Echo later refuses for good is said in the log.
//!
//! A client for any plugin, not only the example's: the log is here too, though the example does not read it.
//! 0BSD, as the rest of the template's own code (LICENSE).
#![allow(dead_code)] // (what the example does not call)
use crate::sys;
use std::collections::VecDeque;
use std::os::fd::{FromRawFd, OwnedFd};
use std::time::{Duration, Instant};

pub const MAX_PACKET: usize = 32768;
const CLIENT_VERSION: u32 = 12;        // (12: a request not of what is spoken refused ERR_NOT_SPOKEN, a value it writes always sent, as its fallback where need be; 11: a value of a later version whose fallback is none not sent at all; 10: a value of a later version than spoken sent as its fallback; 9: the greeting without a set of capability bits, enum values held to the versions spoken)
/// the scopes this client implements, each at the version it speaks (PROTOCOL.md "Versions"): what HELLO offers.  A
/// manifest here may require these, at these versions or lower, and nothing else
pub const SCOPES: [(&str, u16); 9] = [("core", 1), ("storage", 1), ("log.read", 1), ("log.read.full", 1), ("net.server", 1), ("net.lan", 1),
                                      ("net.internet", 1), ("audio.music", 1), ("button", 1)];
/// the scopes a type this client reads is part of (any one of them spoken will do); none: a type it does not read
pub fn scopes_of(kind: u16) -> &'static [&'static str] {
    match kind {
        CONFIG | PING | STOP | TIME | OK | ERR | WELCOME | REFUSED => &["core"],
        STORE_VALUE => &["storage"],
        LOG => &["log.read", "log.read.full"],
        MUSIC_POS | MUSIC_CMD => &["audio.music"],
        BUTTON => &["button"],
        _ => &[],
    }
}
/// the scopes a type this client writes is part of (any one of them spoken will do); none: a type it does not write
pub fn scopes_out(kind: u16) -> &'static [&'static str] {
    match kind {
        HELLO | READY | PONG | OK | ERR => &["core"],
        STORE_GET | STORE_PUT => &["storage"],
        LOG_FOLLOW => &["log.read", "log.read.full"],
        CONNECT => &["net.server", "net.lan", "net.internet"],
        MUSIC_OPEN | MUSIC_STATE | MUSIC_CLOSE => &["audio.music"],
        _ => &[],
    }
}
const CTL: i32 = 3;
const KEPT_BYTES: usize = 2 << 20;
const KEPT_MAX: usize = 1024;
const PENDING: usize = 8;
const RETRY: Duration = Duration::from_secs(10);

// message types (PROTOCOL.md "Messages")
pub const HELLO: u16 = 1;
pub const WELCOME: u16 = 2;
#[allow(dead_code)] // (the answer to a HELLO hassmic cannot serve: anything but WELCOME ends the plugin)
pub const REFUSED: u16 = 3;
pub const CONFIG: u16 = 4;
pub const READY: u16 = 5;
pub const PING: u16 = 6;
pub const PONG: u16 = 7;
pub const STOP: u16 = 8;
pub const TIME: u16 = 9;
pub const OK: u16 = 10;
pub const ERR: u16 = 11;
pub const STORE_GET: u16 = 40;
pub const STORE_VALUE: u16 = 41;
pub const STORE_PUT: u16 = 42;
pub const LOG_FOLLOW: u16 = 50;
pub const LOG: u16 = 51;
pub const CONNECT: u16 = 60;
pub const MUSIC_OPEN: u16 = 70;
pub const MUSIC_STATE: u16 = 71;
pub const MUSIC_CLOSE: u16 = 72;
pub const MUSIC_POS: u16 = 73;
pub const MUSIC_CMD: u16 = 74;
pub const BUTTON: u16 = 120;
// a player's state (MUSIC_STATE), what it is told to do (MUSIC_CMD) and why
pub const MUSIC_PLAYING: u8 = 1;
pub const MUSIC_PAUSED: u8 = 2;
pub const MUSIC_STOPPED: u8 = 3;
pub const MCMD_PAUSE: u8 = 1;
pub const MCMD_RESUME: u8 = 2;
pub const MCMD_STOP: u8 = 3;
pub const MCMD_NEXT: u8 = 4;
pub const MCMD_PREVIOUS: u8 = 5;
// button: which button (BUTTON's key) and how
pub const BUTTON_ACTION: u8 = 1;
pub const BUTTON_VOLUME_UP: u8 = 2;
pub const BUTTON_VOLUME_DOWN: u8 = 3;
pub const BUTTON_PRESS: u8 = 1;
pub const BUTTON_HOLD: u8 = 2;

pub const ERR_UNKNOWN: u16 = 1;
pub const ERR_DENIED: u16 = 2;
pub const ERR_BAD: u16 = 3;
pub const ERR_BUSY: u16 = 4;
pub const ERR_LIMIT: u16 = 5;
pub const ERR_NOT_FOUND: u16 = 6;
pub const ERR_FAILED: u16 = 7;
pub const ERR_UNSET: u16 = 8;
/// the client's own, never hassmic's (65535, which messages.def keeps out of ERR_*): the request was not sent, as its
/// message is of a scope not spoken with hassmic (WELCOME's scopes: its manifest's requires), a request of a
/// capability it does not ask for among them (the C client's HM_ERR_NOT_SPOKEN).  Never for a value: a writer refuses
/// one of no version (ERR_BAD)
pub const ERR_NOT_SPOKEN: u16 = 0xffff;
pub const CAP_LOG_READ_FULL: u8 = 4;
pub const CAP_NET_SERVER: u8 = 5;
pub const CAP_NET_LAN: u8 = 6;
pub const CAP_NET_INTERNET: u8 = 7;
pub const CAP_BUTTON: u8 = 18;

// an enum's values as this client speaks them, every scope at version 1 (PROTOCOL.md "Values, constants and keys"):
// a field that holds one of these enums holds one of these values, else the packet is malformed (a reader's None),
// and a writer refuses another (None, never sent).  A later version of a scope may add values: this client takes them
// only once it speaks that version
pub fn err_ok(v: u16) -> bool { (ERR_UNKNOWN..=ERR_UNSET).contains(&v) }
pub fn music_ok(v: u8) -> bool { (MUSIC_PLAYING..=MUSIC_STOPPED).contains(&v) }
pub fn mcmd_ok(v: u8) -> bool { (MCMD_PAUSE..=MCMD_PREVIOUS).contains(&v) }
pub fn mwhy_ok(v: u8) -> bool { (1..=4).contains(&v) }         // BUTTON, STOP_WORD, OTHER_SOURCE, HOME_ASSISTANT
pub fn button_key_ok(v: u8) -> bool { (BUTTON_ACTION..=BUTTON_VOLUME_DOWN).contains(&v) }
pub fn button_how_ok(v: u8) -> bool { (BUTTON_PRESS..=BUTTON_HOLD).contains(&v) }
/// MUSIC_STATE as it goes to hassmic, which speaks INFO's version of audio.music: a value of a later version than
/// that as its fallback (LATER: MUSIC_LATER), as hassmic sends them; Err: a value no version has (refused).  Never
/// left unsent for its value: a state must arrive, so messages.def gives every later state a fallback (`>-` only on
/// a message marked drop, an event hassmic sends: PROTOCOL.md "Versions")
pub fn music_state_packet(info: &Info, later: &[(u16, u16, u16)], state: u8, title: &str, artist: &str) -> Result<Vec<u8>, ()> {
    write_music_state(0, fit(state as u16, info.scope("audio.music"), later) as u8, title, artist).ok_or(())
}
/// a capability's number (CONNECT's kind): one of the protocol's, storage (0) to button (18)
pub fn cap_ok(v: u8) -> bool { v <= CAP_BUTTON }

// The value a peer that speaks an earlier version gets in place of one a later version of its scope brought
// (PROTOCOL.md "Values, constants and keys": the reference names it beside each such value, "from <scope> N; to a peer
// of an earlier version X"; the vectors' `fit` lines hold it), so that no message is dropped for its value.  A value
// with none ("not sent to a peer of an earlier version", messages.def's `>-`, the vectors' `unsent` lines) is only
// ever on a message marked drop, an event hassmic sends (BUTTON): never on one this client writes, whose every later
// value names its fallback (a review of hassmic 61e3d8e, finding 1; the tests fail on an `unsent` line of one).  Per
// enum this client writes, every value of a later version as (value, the version that brought it, its fallback):
// none yet, and this client speaks version 1 of every scope, whose values every peer has.  A value from one of these
// tables is written as `fit` makes it for the version spoken; the tests hold the tables to the vectors
pub const ERR_LATER: &[(u16, u16, u16)] = &[];
pub const MUSIC_LATER: &[(u16, u16, u16)] = &[];
/// V as a peer speaking version SPOKEN of its enum's scope gets it: V where that version has it, else its fallback,
/// and that one's, down to a value it has (LATER: the enum's table above)
pub fn fit(v: u16, spoken: u16, later: &[(u16, u16, u16)]) -> u16 {
    let mut v = v;
    while let Some(&(_, since, fallback)) = later.iter().find(|x| x.0 == v) {
        if spoken >= since { break; }
        v = fallback;
    }
    v
}

// ---- the wire: little-endian, a string or bytes a u16 length and that many bytes

/// a message being written: the header, then fields
pub struct W(pub Vec<u8>);
impl W {
    pub fn new(kind: u16, id: u32) -> W {
        let mut w = W(Vec::with_capacity(64));
        w.u16(kind).u16(0).u32(id);
        w
    }
    pub fn u8(&mut self, v: u8) -> &mut W { self.0.push(v); self }
    pub fn u16(&mut self, v: u16) -> &mut W { self.0.extend_from_slice(&v.to_le_bytes()); self }
    pub fn u32(&mut self, v: u32) -> &mut W { self.0.extend_from_slice(&v.to_le_bytes()); self }
    pub fn u64(&mut self, v: u64) -> &mut W { self.0.extend_from_slice(&v.to_le_bytes()); self }
    /// a str field (no NUL, at most MAX bytes) or bytes: a length and the bytes; None when it does not fit, and the
    /// message is not to be sent then (a cut message is never sent)
    pub fn bytes(&mut self, b: &[u8], max: usize) -> Option<&mut W> {
        if b.len() > max { return None; }
        self.u16(b.len() as u16);
        self.0.extend_from_slice(b);
        Some(self)
    }
    pub fn str(&mut self, s: &str, max: usize) -> Option<&mut W> {
        if s.as_bytes().contains(&0) { return None; }
        self.bytes(s.as_bytes(), max)
    }
}

/// a message being read: None at the first field that is not there or breaks its rule
pub struct R<'a> { b: &'a [u8], p: usize }
impl<'a> R<'a> {
    pub fn new(b: &'a [u8]) -> R<'a> { R { b, p: 0 } }
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        let s = self.b.get(self.p..self.p.checked_add(n)?)?;
        self.p += n;
        Some(s)
    }
    pub fn u8(&mut self) -> Option<u8> { Some(self.take(1)?[0]) }
    pub fn u16(&mut self) -> Option<u16> { Some(u16::from_le_bytes(self.take(2)?.try_into().ok()?)) }
    pub fn u32(&mut self) -> Option<u32> { Some(u32::from_le_bytes(self.take(4)?.try_into().ok()?)) }
    pub fn u64(&mut self) -> Option<u64> { Some(u64::from_le_bytes(self.take(8)?.try_into().ok()?)) }
    pub fn bytes(&mut self, max: usize) -> Option<&'a [u8]> {
        let n = self.u16()? as usize;
        if n > max { return None; }
        self.take(n)
    }
    pub fn str(&mut self, max: usize) -> Option<String> {
        let b = self.bytes(max)?;
        if b.contains(&0) { return None; }
        Some(String::from_utf8_lossy(b).into_owned())
    }
    /// the end of the packet: nothing after the last field (a packet is exactly its layout at the version spoken)
    pub fn end(&self) -> Option<()> { if self.p == self.b.len() { Some(()) } else { None } }
}

/// the header: type, id, the fields
pub fn head(p: &[u8]) -> Option<(u16, u32, &[u8])> {
    let mut r = R::new(p);
    let (kind, _flags, id) = (r.u16()?, r.u16()?, r.u32()?);
    Some((kind, id, &p[8..]))
}

#[derive(Debug, PartialEq)]
pub struct Welcome { pub clock_set: u32, pub model: String, pub node: String, pub name: String, pub zone: String, pub scopes: Vec<(String, u16)> }
/// the greeting's lists of scopes at their bound, for good: every scope there will ever be (PROTOCOL.md "Versions")
pub const SCOPES_MAX: usize = 255;
/// WELCOME: where it runs, and the scopes spoken from here on, which is also what was granted (its list's elements
/// after the last field)
pub fn read_welcome(b: &[u8]) -> Option<Welcome> {
    let mut r = R::new(b);
    let (clock_set, model, node, name, zone) = (r.u32()?, r.str(48)?, r.str(64)?, r.str(64)?, r.str(64)?);
    let n = r.u16()? as usize;
    if n > SCOPES_MAX { return None; }
    let scopes = (0..n).map(|_| Some((r.str(24)?, r.u16()?))).collect::<Option<Vec<_>>>()?;
    r.end()?;
    Some(Welcome { clock_set, model, node, name, zone, scopes })
}

/// CONFIG: the settings, (name, value)
pub fn read_config(b: &[u8]) -> Option<Vec<(String, String)>> {
    let mut r = R::new(b);
    let n = r.u16()? as usize;
    if n > 32 { return None; }
    let s = (0..n).map(|_| Some((r.str(32)?, r.str(512)?))).collect::<Option<Vec<_>>>()?;
    r.end()?;
    Some(s)
}

/// LOG: cursor, gap, lines
pub fn read_log(b: &[u8]) -> Option<(String, u8, Vec<String>)> {
    let mut r = R::new(b);
    let cursor = r.str(40)?;
    let gap = r.u8()?;
    let n = r.u16()? as usize;
    if n > 64 { return None; }
    let lines = (0..n).map(|_| r.str(1024)).collect::<Option<Vec<_>>>()?;
    r.end()?;
    Some((cursor, gap, lines))
}

// the messages this client sends, each from all its fields (None: a field that breaks its rule, never sent)
/// HELLO: the client's version, and every scope it speaks at the version it has (each once; SCOPES_MAX at most)
pub fn write_hello(id: u32, sdk: u32, scopes: &[(&str, u16)]) -> Option<Vec<u8>> {
    if scopes.len() > SCOPES_MAX { return None; }
    let mut w = W::new(HELLO, id);
    w.u32(sdk).u16(scopes.len() as u16);
    for (name, v) in scopes { w.str(name, 24)?.u16(*v); }
    Some(w.0)
}
pub fn write_connect(id: u32, kind: u8, entry: u8, host: &str, port: u16) -> Option<Vec<u8>> {
    if !cap_ok(kind) { return None; }
    let mut w = W::new(CONNECT, id);
    w.u8(kind).u8(entry).str(host, 253)?.u16(port);
    Some(w.0)
}
pub fn write_store_get(id: u32, key: &str) -> Option<Vec<u8>> { Some(W::new(STORE_GET, id).str(key, 48)?.0.clone()) }
pub fn write_store_put(id: u32, key: &str, value: &[u8]) -> Option<Vec<u8>> { Some(W::new(STORE_PUT, id).str(key, 48)?.bytes(value, 16384)?.0.clone()) }
pub fn write_log_follow(id: u32, from: &str) -> Option<Vec<u8>> { Some(W::new(LOG_FOLLOW, id).str(from, 40)?.0.clone()) }
pub fn write_err(id: u32, code: u16) -> Option<Vec<u8>> { if err_ok(code) { Some(W::new(ERR, id).u16(code).0.clone()) } else { None } }
pub fn write_empty(kind: u16, id: u32) -> Vec<u8> { W::new(kind, id).0 }  // READY, PONG, OK (a LOG handled)

/// TIME: clock_set, zone
pub fn read_time(b: &[u8]) -> Option<(u32, String)> { let mut r = R::new(b); let t = (r.u32()?, r.str(64)?); r.end()?; Some(t) }
/// ERR: its code
pub fn read_err(b: &[u8]) -> Option<u16> { let mut r = R::new(b); let c = r.u16()?; r.end()?; if err_ok(c) { Some(c) } else { None } }
/// STORE_VALUE: the value
pub fn read_store_value(b: &[u8]) -> Option<Vec<u8>> { let mut r = R::new(b); let v = r.bytes(16384)?.to_vec(); r.end()?; Some(v) }
// music: a stream asked for, the player's state, the stream's end; where it is, what to do
pub fn write_music_open(id: u32, rate: u32, channels: u8) -> Vec<u8> { W::new(MUSIC_OPEN, id).u32(rate).u8(channels).0.clone() }
pub fn write_music_state(id: u32, state: u8, title: &str, artist: &str) -> Option<Vec<u8>> {
    if !music_ok(state) { return None; }
    Some(W::new(MUSIC_STATE, id).u8(state).str(title, 128)?.str(artist, 128)?.0.clone())
}
pub fn read_music_pos(b: &[u8]) -> Option<(u64, u64, u32)> { let mut r = R::new(b); let p = (r.u64()?, r.u64()?, r.u32()?); r.end()?; Some(p) }
pub fn read_music_cmd(b: &[u8]) -> Option<(u8, u8)> { let mut r = R::new(b); let c = (r.u8()?, r.u8()?); r.end()?; if mcmd_ok(c.0) && mwhy_ok(c.1) { Some(c) } else { None } }
/// BUTTON: key, how
pub fn read_button(b: &[u8]) -> Option<(u8, u8)> { let mut r = R::new(b); let k = (r.u8()?, r.u8()?); r.end()?; if button_key_ok(k.0) && button_how_ok(k.1) { Some(k) } else { None } }
/// a packet of no fields (OK, PING, STOP): nothing in its body
pub fn read_empty(b: &[u8]) -> Option<()> { R::new(b).end() }

// ---- the client

#[allow(dead_code)] // (what WELCOME says: this plugin reads the node, the rest is the client's to offer)
pub struct Info { pub clock_set: bool, pub model: String, pub node: String, pub name: String, pub zone: String,
                  /// the scopes spoken with hassmic and their versions (WELCOME's: the manifest's requires)
                  pub scopes: Vec<(String, u16)> }
impl Info {
    /// the version of scope NAME spoken with hassmic, 0 none
    pub fn scope(&self, name: &str) -> u16 { self.scopes.iter().find(|(n, _)| n == name).map_or(0, |s| s.1) }
    /// whether a type this client reads is of a scope spoken
    pub fn speaks(&self, kind: u16) -> bool { scopes_of(kind).iter().any(|s| self.scope(s) >= 1) }
    /// whether a type this client writes is of a scope spoken (else the request is refused here, ERR_NOT_SPOKEN)
    pub fn sends(&self, kind: u16) -> bool { scopes_out(kind).iter().any(|s| self.scope(s) >= 1) }
    /// whether the owner accepted capability CAP (its manifest's name, "net.server"): its scope is spoken
    pub fn granted(&self, cap: &str) -> bool { cap != "core" && self.scope(cap) >= 1 }
}

/// WELCOME's scopes, as this client takes them: each one it has (SCOPES), at its version or lower, each once, core
/// among them.  None: a WELCOME no hassmic that read this client's HELLO sends
pub fn welcome_scopes(w: &Welcome) -> Option<Vec<(String, u16)>> {
    let mut seen: Vec<(String, u16)> = Vec::new();
    for (name, v) in &w.scopes {
        let have = SCOPES.iter().find(|(n, _)| n == name)?.1;
        if *v == 0 || *v > have || seen.iter().any(|(n, _)| n == name) { return None; }
        seen.push((name.clone(), *v));
    }
    if !seen.iter().any(|(n, _)| n == "core") { return None; }
    Some(seen)
}

/// what a plugin does with what hassmic says; every callback runs on run()'s thread, and may call the client
pub trait Plugin {
    fn config(&mut self, _c: &mut Client, _settings: &[(String, String)]) {}
    fn ready(&mut self, _c: &mut Client) {}
    fn log(&mut self, _c: &mut Client, _cursor: &str, _gap: bool, _lines: &[String]) {}
    /// about once a second once it runs
    fn tick(&mut self, _c: &mut Client) {}
    fn time(&mut self, _c: &mut Client) {}
    fn stop(&mut self, _c: &mut Client) {}
    /// audio.music: the stream `music_open` gave takes more (polled for writing while it is open and the last
    /// `music_state` said playing): write what it takes; it is non-blocking, "would block" is full
    fn music_writable(&mut self, _c: &mut Client, _fd: i32) {}
    /// audio.music: do this (MCMD_*) to what plays, because of WHY (1 the button, 2 the stop word, 3 another source or
    /// a call, 4 Home Assistant's media player); say what the player does after it with `music_state`
    fn music_cmd(&mut self, _c: &mut Client, _cmd: u8, _why: u8) {}
    /// audio.music: FRAMES reached the speaker at AT_US (CLOCK_MONOTONIC), QUEUED_US written and not played yet
    fn music_pos(&mut self, _c: &mut Client, _frames: u64, _at_us: u64, _queued_us: u32) {}
    /// button: KEY (BUTTON_ACTION, _VOLUME_UP, _VOLUME_DOWN) pressed (BUTTON_PRESS) or held a second
    /// (BUTTON_HOLD), as Home Assistant's "Button" event entity hears it: never a press that served a wait for the
    /// button (a login, a plugin's install, a phone's setup), nor a hold that served the settings page's note.  The
    /// plugin only hears: the button does on the Echo what it does anyway
    fn button(&mut self, _c: &mut Client, _key: u8, _how: u8) {}
}

/// old: a LOG of the place followed before, put aside while `log_follow` waits
struct Packet { data: Vec<u8>, fd: Option<OwnedFd>, old: bool }

pub struct Client {
    pub info: Info,
    ctl: i32,                   // the control socket: descriptor 3 (run), another in the tests (run_on)
    next_id: u32,
    buf: Vec<u8>,
    kept: VecDeque<Packet>,
    kept_bytes: usize,
    follow_wait: bool,
    running: bool,
    /// a STOP came (kept for the loop, or handled): hassmic answers nothing but storage writes from here
    stop_heard: bool,
    pend: Vec<(String, Vec<u8>)>,
    pend_at: Option<Instant>,
    stopping: bool,
    gone: bool,
    /// the stream music_open gave, and whether the player says it plays (the loop polls the stream for writing then:
    /// a writable stream that a paused player does not write to would have the loop spin)
    music: Option<OwnedFd>,
    music_playing: bool,
}

/// what a request came back with
enum Answer { Ok(Option<OwnedFd>), Err(u16), Value(Vec<u8>), Gone, Stopped }

/// hm_log's equivalent: one line into hassmic's log, as "plugin <id>: ..."; 50 a minute are kept
pub fn log(line: &str) {
    let mut b = line.replace('\n', " ").into_bytes();
    b.truncate(398);
    b.push(b'\n');
    use std::io::Write;
    let _ = std::io::stderr().write_all(&b); // (unbuffered: one write)
}

impl Client {
    fn new_id(&mut self) -> u32 {
        self.next_id = self.next_id.wrapping_add(1);
        if self.next_id == 0 { self.next_id = 1; }
        self.next_id
    }

    fn put(&mut self, msg: &[u8]) -> bool {
        msg.len() <= MAX_PACKET && sys::send_fd(self.ctl, msg, sys::MSG_NOSIGNAL).map(|n| n == msg.len()).unwrap_or(false)
    }

    /// one packet and its descriptor; None when hassmic went away
    fn get(&mut self) -> Option<Packet> {
        match sys::recv_packet(self.ctl, &mut self.buf) {
            Ok((0, fd)) => { fd.map(|d| unsafe { OwnedFd::from_raw_fd(d) }); None }
            Ok((n, fd)) => Some(Packet { data: self.buf[..n].to_vec(), fd: fd.map(|d| unsafe { OwnedFd::from_raw_fd(d) }), old: false }),
            Err(_) => None,
        }
    }

    fn keep(&mut self, mut p: Packet) {
        p.old = self.follow_wait && head(&p.data).map(|h| h.0) == Some(LOG);      // (the old place's: log_follow)
        if self.kept.len() == KEPT_MAX || self.kept_bytes + p.data.len() > KEPT_BYTES {
            log("the plugin's client cannot keep what hassmic sends while a request waits: it ends (client.rs KEPT_BYTES)");
            std::process::exit(70); // (counted as a crash and started again: nothing of it is lost unsaid)
        }
        self.kept_bytes += p.data.len();
        self.kept.push_back(p);
    }

    /// waits for the answer to request ID: PINGs answered meanwhile, the rest kept for the loop.  WRITE: a storage
    /// write, which hassmic answers after STOP too; any other request ends its wait at a STOP (Answer::Stopped)
    fn answer(&mut self, id: u32, write: bool) -> Answer {
        loop {
            let Some(p) = self.get() else { self.gone = true; return Answer::Gone };
            let Some((kind, pid, body)) = head(&p.data) else { continue };
            // (an OK with a body is malformed: no answer this client takes, the request refused)
            if pid == id && kind == OK { return if body.is_empty() { Answer::Ok(p.fd) } else { Answer::Err(ERR_BAD) }; }
            if pid == id && kind == ERR { return Answer::Err(read_err(body).unwrap_or(ERR_UNKNOWN)); }
            if pid == id && kind == STORE_VALUE {
                return match read_store_value(body) { Some(v) => Answer::Value(v), None => Answer::Err(ERR_BAD) };
            }
            if kind == PING {
                if read_empty(body).is_some() { let m = write_empty(PONG, pid); self.put(&m); }
                continue;
            }
            let stop = kind == STOP && read_empty(body).is_some();
            self.keep(p);
            if stop { self.stop_heard = true; if !write { return Answer::Stopped; } }
        }
    }

    fn request(&mut self, msg: Option<Vec<u8>>, write: bool) -> Answer {
        let Some(msg) = msg else { return Answer::Err(ERR_BAD) };
        if !head(&msg).map_or(false, |h| self.info.sends(h.0)) { return Answer::Err(ERR_NOT_SPOKEN); }
        if self.stop_heard && !write { return Answer::Stopped; }     // (hassmic would answer it FAILED)
        if !self.put(&msg) { return Answer::Err(ERR_BAD); }
        let id = u32::from_le_bytes(msg[4..8].try_into().unwrap());
        self.answer(id, write)
    }

    /// storage: Ok(Some(value)), Ok(None) no such key, Err(code)
    pub fn store_get(&mut self, key: &str) -> Result<Option<Vec<u8>>, u16> {
        if let Some((_, v)) = self.pend.iter().find(|(k, _)| k == key) { return Ok(Some(v.clone())); }
        let id = self.new_id();
        match self.request(write_store_get(id, key), false) {
            Answer::Value(v) => Ok(Some(v)),
            Answer::Err(ERR_NOT_FOUND) => Ok(None),
            Answer::Err(c) => Err(c),
            Answer::Stopped => Err(ERR_FAILED),
            _ => Err(ERR_UNKNOWN),
        }
    }

    fn store_put_now(&mut self, key: &str, v: &[u8]) -> Result<bool, u16> {
        if v.len() > 16384 { return Err(ERR_LIMIT); }
        let id = self.new_id();
        match self.request(write_store_put(id, key, v), true) {
            Answer::Ok(_) => Ok(true),
            Answer::Err(ERR_BUSY) => Ok(false),
            Answer::Err(c) => Err(c),
            _ => Err(ERR_FAILED),
        }
    }

    /// storage: Ok(true) written, Ok(false) the Echo takes no write now and the client writes the latest value of
    /// the key when it does (10 writes, then one a minute: the flash), Err(ERR_BUSY) neither written nor kept (no
    /// room for another key), Err(code) refused
    pub fn store_put(&mut self, key: &str, v: &[u8]) -> Result<bool, u16> {
        // stopping: now or not at all (nothing is written later by a client whose loop ends after this callback); a
        // value kept for the key before is this one's elder, and goes
        if self.stop_heard {
            self.pend.retain(|(k, _)| k != key);
            return if self.store_put_now(key, v)? { Ok(true) } else { Err(ERR_BUSY) };
        }
        if let Some(i) = self.pend.iter().position(|(k, _)| k == key) { self.pend[i].1 = v.to_vec(); return Ok(false); }
        let r = self.store_put_now(key, v)?;
        if !r {
            if self.pend.len() == PENDING { return Err(ERR_BUSY); }
            self.pend.push((key.to_string(), v.to_vec()));
            self.pend_at.get_or_insert(Instant::now() + RETRY);
        }
        Ok(r)
    }

    fn pend_try(&mut self) {
        let Some(at) = self.pend_at else { return };
        if Instant::now() < at { return; }
        while let Some((k, v)) = self.pend.first().cloned() {
            match self.store_put_now(&k, &v) {
                Ok(false) => { self.pend_at = Some(Instant::now() + RETRY); return; }
                Err(why) => log(&format!("storage: the value of \"{k}\" kept to be written later was refused (error {why}): not stored")),
                Ok(true) => {}
            }
            self.pend.remove(0);
        }
        self.pend_at = None;
    }

    /// run ends: what is kept for later tried once more (hassmic answers storage writes until the plugin is gone), and
    /// what still does not go said in the log: told "kept, written later", it would be lost without a word (R4)
    fn pend_flush(&mut self) {
        for (k, v) in std::mem::take(&mut self.pend) {
            match self.store_put_now(&k, &v) {
                Ok(true) => {}
                Ok(false) => log(&format!("storage: the value of \"{k}\" kept to be written later is not stored: the plugin ends (error {ERR_BUSY})")),
                Err(why) => log(&format!("storage: the value of \"{k}\" kept to be written later is not stored: the plugin ends (error {why})")),
            }
        }
        self.pend_at = None;
    }

    /// the log from FROM on (a cursor of a LOG before, "start", "" for now): Ok, or the code it was refused with
    /// (and the log goes on from where it was).  The LOGs that came before the answer were sent from the old place:
    /// put aside, kept ones and those meanwhile; dropped when hassmic took the new place, handed on when it did not
    pub fn log_follow(&mut self, from: &str) -> Result<(), u16> {
        let id = self.new_id();
        let w = write_log_follow(id, from);
        if w.is_none() { return Err(ERR_BAD); }
        if self.stop_heard { return Err(ERR_FAILED); }
        for p in self.kept.iter_mut() { p.old = head(&p.data).map(|h| h.0) == Some(LOG); }
        self.follow_wait = true;
        let a = self.request(w, false);
        self.follow_wait = false;
        let took = matches!(a, Answer::Ok(_));
        let kept = std::mem::take(&mut self.kept);
        self.kept = kept.into_iter().filter(|p| !(took && p.old)).map(|mut p| { p.old = false; p }).collect();
        self.kept_bytes = self.kept.iter().map(|p| p.data.len()).sum();
        match a {
            Answer::Ok(_) => Ok(()),
            Answer::Err(c) => Err(c),
            Answer::Stopped => Err(ERR_FAILED),
            _ => Err(ERR_UNKNOWN),
        }
    }

    /// a socket to the server of the ENTRY'th net.server entry, at its port (0): connected, non-blocking, ours to
    /// close; or the code (ERR_UNSET, ERR_NOT_FOUND, ERR_FAILED, ERR_DENIED, ERR_BUSY)
    pub fn connect(&mut self, entry: u8, port: u16) -> Result<OwnedFd, u16> { self.connect_kind(CAP_NET_SERVER, entry, "", port) }

    /// net.lan: a socket to ADDRESS ("a.b.c.d", private or link-local, never a name) by the ENTRY'th net.lan
    /// entry, at PORT (0: the entry's only port); as connect otherwise (ERR_DENIED: not an address or port the entry
    /// allows; ERR_BAD: no address, or port 0 of several)
    pub fn connect_lan(&mut self, entry: u8, address: &str, port: u16) -> Result<OwnedFd, u16> { self.connect_kind(CAP_NET_LAN, entry, address, port) }

    /// net.internet: a socket to HOST (a name or an IPv4 address, anywhere) by the ENTRY'th net.internet
    /// entry, at PORT; as connect otherwise (ERR_NOT_FOUND: the name has no address)
    pub fn connect_internet(&mut self, entry: u8, host: &str, port: u16) -> Result<OwnedFd, u16> { self.connect_kind(CAP_NET_INTERNET, entry, host, port) }

    fn connect_kind(&mut self, kind: u8, entry: u8, host: &str, port: u16) -> Result<OwnedFd, u16> {
        if self.stop_heard { return Err(ERR_FAILED); }
        if !self.running { return Err(ERR_BAD); } // (hassmic answers BAD before READY: never asked there)
        let id = self.new_id();
        if !self.info.sends(CONNECT) { return Err(ERR_NOT_SPOKEN); }
        let m = match write_connect(id, kind, entry, host, port) { Some(m) => m, None => return Err(ERR_BAD) };
        if !self.put(&m) { return Err(ERR_BAD); }
        match self.answer(id, false) {
            Answer::Ok(Some(fd)) => Ok(fd),
            Answer::Err(c) => Err(c),
            Answer::Stopped => Err(ERR_FAILED),
            _ => Err(ERR_UNKNOWN),
        }
    }

    /// audio.music: a stream to the Echo's speaker, s16le at RATE (8000 to 48000) and CHANNELS (1, 2); its descriptor
    /// (kept by the client, non-blocking), or the code (ERR_BUSY open already, ERR_LIMIT ten in a minute, ERR_BAD).
    /// Only once the plugin runs, as CONNECT
    pub fn music_open(&mut self, rate: u32, channels: u8) -> Result<i32, u16> {
        if self.stop_heard { return Err(ERR_FAILED); }
        if !self.running { return Err(ERR_BAD); }
        let id = self.new_id();
        match self.request(Some(write_music_open(id, rate, channels)), false) {
            Answer::Ok(Some(fd)) => {
                use std::os::fd::AsRawFd;
                let raw = fd.as_raw_fd();
                self.music = Some(fd); self.music_playing = true;        // (hassmic takes an open stream as playing)
                Ok(raw)
            }
            Answer::Err(c) => Err(c),
            Answer::Stopped => Err(ERR_FAILED),
            _ => Err(ERR_UNKNOWN),
        }
    }

    /// audio.music: what the player does now (MUSIC_*), with a title and an artist for its card; not waited for
    /// (false: not sent, nothing changes: a state MUSIC_* has not, or audio.music not spoken)
    pub fn music_state(&mut self, state: u8, title: &str, artist: &str) -> bool {
        let sent = self.info.sends(MUSIC_STATE) && match music_state_packet(&self.info, MUSIC_LATER, state, title, artist) { Ok(m) => self.put(&m), Err(()) => false };
        if sent { self.music_playing = state == MUSIC_PLAYING; }
        sent
    }

    /// audio.music: the stream ends (what was written plays out); the descriptor is closed
    pub fn music_close(&mut self) -> Result<(), u16> {
        if self.music.is_none() || self.stop_heard { return Err(ERR_BAD); }
        let id = self.new_id();
        let r = self.request(Some(write_empty(MUSIC_CLOSE, id)), false);
        self.music = None; self.music_playing = false;
        match r { Answer::Ok(_) => Ok(()), Answer::Err(c) => Err(c), _ => Err(ERR_FAILED) }
    }

    fn handle<P: Plugin>(&mut self, p: Packet, plugin: &mut P, ready_said: &mut bool) {
        let Some((kind, id, body)) = head(&p.data) else { return };
        // (of a scope not spoken with hassmic: as a type it does not know, never acted on)
        let kind = if self.info.speaks(kind) { kind } else { 0 };
        match kind {
            CONFIG => {
                let Some(s) = read_config(body) else { return };
                plugin.config(self, &s);
                if !*ready_said {
                    // the first one closes the start
                    *ready_said = true;
                    plugin.ready(self);
                    let m = write_empty(READY, 0);
                    self.put(&m);
                    self.running = true;
                }
            }
            PING => { if read_empty(body).is_some() { let m = write_empty(PONG, id); self.put(&m); } }
            LOG => {
                if let Some((c, g, l)) = read_log(body) { plugin.log(self, &c, g != 0, &l); }
                // handled: hassmic sends the next one after this OK (flow control)
                if id != 0 { let m = write_empty(OK, id); self.put(&m); }
            }
            STOP => { if read_empty(body).is_none() { return; } self.stopping = true; self.stop_heard = true; self.running = false; plugin.stop(self); }
            MUSIC_POS => { if let Some((f, at, q)) = read_music_pos(body) { plugin.music_pos(self, f, at, q); } }
            MUSIC_CMD => { if let Some((cmd, why)) = read_music_cmd(body) { plugin.music_cmd(self, cmd, why); } }
            BUTTON => { if let Some((key, how)) = read_button(body) { plugin.button(self, key, how); } }
            TIME => {
                if let Some((c, z)) = read_time(body) { self.info.clock_set = c != 0; self.info.zone = z; plugin.time(self); }
            }
            OK | ERR | STORE_VALUE => {} // an answer nobody waits for any more
            _ => {
                // a type it does not read, or of a scope not spoken: said so, never a reason to end
                if id != 0 { if let Some(m) = write_err(id, fit(ERR_UNKNOWN, self.info.scope("core"), ERR_LATER)) { self.put(&m); } }
            }
        }
        drop(p.fd);
    }
}

/// the plugin's life: 0 when hassmic said to stop, 1 when it refused the plugin or went away
pub fn run<P: Plugin>(plugin: &mut P) -> i32 {
    sys::sigsys_handler();
    run_on(CTL, plugin)
}

/// the same over the control socket CTL (the tests' stand-in broker holds its other end)
fn run_on<P: Plugin>(ctl: i32, plugin: &mut P) -> i32 {
    let mut c = Client {
        ctl,
        info: Info { clock_set: false, model: String::new(), node: String::new(), name: String::new(), zone: String::new(), scopes: Vec::new() },
        next_id: 0, buf: vec![0; MAX_PACKET], kept: VecDeque::new(), kept_bytes: 0, follow_wait: false, running: false, stop_heard: false,
        pend: Vec::new(), pend_at: None, stopping: false, gone: false, music: None, music_playing: false,
    };
    let id = c.new_id();
    if !c.put(&write_hello(id, CLIENT_VERSION, &SCOPES).unwrap()) { return 1; }
    let Some(p) = c.get() else { return 1 };
    match head(&p.data) {
        Some((WELCOME, _, b)) => match read_welcome(b) {
            Some(w) => {
                let Some(scopes) = welcome_scopes(&w) else {
                    log("plugin client: hassmic speaks a scope this client does not have");
                    return 1;
                };
                c.info = Info { clock_set: w.clock_set != 0, model: w.model, node: w.node, name: w.name, zone: w.zone, scopes }
            }
            None => return 1,
        },
        _ => return 1,
    }
    let mut ready_said = false;
    let mut tick_at = Instant::now();
    loop {
        // what came while a request waited, in its order, before anything newer; nothing after a STOP (its callback
        // was the last)
        while !c.stopping {
            let Some(p) = c.kept.pop_front() else { break };
            c.kept_bytes -= p.data.len();
            c.handle(p, plugin, &mut ready_said);
        }
        if c.stopping { c.kept.clear(); c.kept_bytes = 0; c.pend_flush(); return 0; }   // (what came after STOP is no one's)
        if c.gone { return 1; }
        c.pend_try();
        let now = Instant::now();
        // the tick, then the socket in any case: a tick slower than its second must not keep PING and STOP unread
        let mut ticked = false;
        if c.running && now >= tick_at {
            tick_at = now + Duration::from_secs(1);
            plugin.tick(&mut c);
            if !c.kept.is_empty() || c.stopping || c.gone { continue; }
            ticked = true;
        }
        let mut next: Option<Instant> = if c.running { Some(tick_at) } else { None };
        if ticked { next = Some(Instant::now()); } // (after a tick: what came meanwhile, at once)
        if let Some(a) = c.pend_at { next = Some(next.map_or(a, |n| n.min(a))); }
        let timeout = next.map_or(-1, |n| n.saturating_duration_since(now).as_millis().min(i32::MAX as u128) as i32);
        // (a negative descriptor is left out by poll: the stream only while it is open and the player plays)
        let mfd = match (&c.music, c.music_playing && c.running) { (Some(f), true) => { use std::os::fd::AsRawFd; f.as_raw_fd() } _ => -1 };
        let mut pf = [sys::pollfd { fd: c.ctl, events: sys::POLLIN, revents: 0 }, sys::pollfd { fd: mfd, events: sys::POLLOUT, revents: 0 }];
        match sys::poll_fds(&mut pf, timeout) {
            Err(sys::EINTR) => continue,
            Err(_) => return 1,
            Ok(_) => {}
        }
        if mfd >= 0 && pf[1].revents & (sys::POLLOUT | sys::POLLERR | sys::POLLHUP) != 0 {
            plugin.music_writable(&mut c, mfd);
            if !c.kept.is_empty() || c.stopping || c.gone { continue; }
        }
        if pf[0].revents & sys::POLLIN != 0 {
            // everything that waits, up to a bound; never past what a callback's request kept meanwhile, which is older
            // and is handed on first (R3: read on here, a LOG overtook the one kept before it)
            for k in 0..64 {
                if c.stopping || !c.kept.is_empty() { break; }
                if k > 0 {
                    let mut q = [sys::pollfd { fd: c.ctl, events: sys::POLLIN, revents: 0 }];
                    if sys::poll_fds(&mut q, 0) != Ok(1) || q[0].revents & sys::POLLIN == 0 { break; }
                }
                let Some(p) = c.get() else { return 1 };
                c.handle(p, plugin, &mut ready_said);
            }
        } else if pf[0].revents & (sys::POLLHUP | sys::POLLERR) != 0 {
            return 1;
        }
    }
}

#[cfg(test)]
mod tests {
    //! The wire format against hassmic's test vectors (sdk/vectors/*.vec, PROTOCOL.md "Test vectors", taken by
    //! tools/sync.sh): every message this client writes made from each case's fields (the sample, `empty`, `long`,
    //! `full`) and held to its packet, every message it reads read from each case's packet and held to the fields;
    //! every `refuse` packet of a message it reads refused, and for one it writes, a value over the field's bound or
    //! with a NUL refused by its writer; and a few cases written down by hand.
    use super::*;
    use std::collections::HashMap;

    struct Vector { name: String, case: String, number: u16, id: u32, version: Option<(String, u16)>, fields: Vec<(String, String, String)>, packet: Vec<u8> }
    struct Refusal { name: String, number: u16, what: String, packet: Vec<u8> }

    fn unhex(h: &str) -> Vec<u8> {
        if h == "-" { return Vec::new(); }
        (0..h.len()).step_by(2).map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap()).collect()
    }

    fn vectors() -> (Vec<Vector>, Vec<Refusal>) { vectors_in(concat!(env!("CARGO_MANIFEST_DIR"), "/../sdk/vectors"), true) }
    /// the vectors of DIR (WHOLE: hassmic's, every message's with its edges and refusals)
    fn vectors_in(dir: &str, whole: bool) -> (Vec<Vector>, Vec<Refusal>) {
        let (mut out, mut refused, mut files) = (Vec::new(), Vec::new(), 0);
        for e in std::fs::read_dir(dir).expect("sdk/vectors/ (tools/sync.sh takes it from hassmic's tree)") {
            let p = e.unwrap().path();
            if p.extension().and_then(|x| x.to_str()) != Some("vec") { continue; }
            files += 1;
            let (mut name, mut number) = (String::new(), 0u16);
            let mut v: Option<Vector> = None;
            for line in std::fs::read_to_string(&p).unwrap().lines() {
                let line = line.split('#').next().unwrap().trim();
                let w: Vec<&str> = line.split_whitespace().collect();
                match w.as_slice() {
                    [] => {}
                    ["message", n, k] => {
                        name = n.to_string(); number = k.parse().unwrap();
                        v = Some(Vector { name: name.clone(), case: "sample".into(), number, id: 0, version: None, fields: Vec::new(), packet: Vec::new() });
                    }
                    ["case", c] => {
                        out.extend(v.take());
                        v = Some(Vector { name: name.clone(), case: c.to_string(), number, id: 0, version: None, fields: Vec::new(), packet: Vec::new() });
                    }
                    ["refuse", what, h] => { out.extend(v.take()); refused.push(Refusal { name: name.clone(), number, what: what.to_string(), packet: unhex(h) }); }
                    // (a value of a later version and what a sender sends a peer of an earlier one: fits() reads them)
                    ["fit", _, _, _] | ["unsent", _, _] => { out.extend(v.take()); }
                    ["id", i] => v.as_mut().unwrap().id = i.parse().unwrap(),
                    // (a case at another version of its scope: none this client reads at version 1)
                    ["version", sc, n] => v.as_mut().unwrap().version = Some((sc.to_string(), n.parse().unwrap())),
                    ["packet", h] => v.as_mut().unwrap().packet = unhex(h),
                    [path, ty, val] => v.as_mut().unwrap().fields.push((path.to_string(), ty.to_string(), val.to_string())),
                    _ => panic!("{}: a line this test does not read: {line}", p.display()),
                }
            }
            out.extend(v.take());
        }
        assert!(!whole || files >= 30, "every message has its vector");
        assert!(!whole || (out.iter().any(|v| v.case == "full") && !refused.is_empty()), "the vectors have their edges and refusals (hassmic after 0dabfa6's review)");
        // (every version this client has is 1: a case of another version is not its to write or read)
        let out: Vec<Vector> = out.into_iter().filter(|v| v.version.as_ref().map_or(true, |(_, n)| *n == 1)).collect();
        (out, refused)
    }

    /// every `fit` and `unsent` line on file: (message, "path:scope:version", the packet with a value of a later
    /// version, the packet a sender sends a peer of that version: the value's fallback in its place; None for an
    /// `unsent` line, a value whose fallback is none: not sent)
    fn fits() -> Vec<(String, String, Vec<u8>, Option<Vec<u8>>)> { fits_in(concat!(env!("CARGO_MANIFEST_DIR"), "/../sdk/vectors")) }
    fn fits_in(dir: &str) -> Vec<(String, String, Vec<u8>, Option<Vec<u8>>)> {
        let mut out = Vec::new();
        for e in std::fs::read_dir(dir).unwrap() {
            let p = e.unwrap().path();
            if p.extension().and_then(|x| x.to_str()) != Some("vec") { continue; }
            let name = p.file_stem().unwrap().to_str().unwrap().to_string();
            for line in std::fs::read_to_string(&p).unwrap().lines() {
                match line.split_whitespace().collect::<Vec<_>>().as_slice() {
                    ["fit", what, a, b] => out.push((name.clone(), what.to_string(), unhex(a), Some(unhex(b)))),
                    ["unsent", what, a] => out.push((name.clone(), what.to_string(), unhex(a), None)),
                    _ => {}
                }
            }
        }
        out
    }

    impl Vector {
        fn f(&self) -> HashMap<&str, &str> { self.fields.iter().map(|(p, _, v)| (p.as_str(), v.as_str())).collect() }
        fn num<T: std::str::FromStr>(&self, k: &str) -> T where T::Err: std::fmt::Debug { self.f()[k].parse().unwrap() }
        fn bytes(&self, k: &str) -> Vec<u8> { unhex(self.f()[k]) }
        fn text(&self, k: &str) -> String { String::from_utf8(self.bytes(k)).unwrap() }
    }

    const WRITES: [&str; 11] = ["HELLO", "CONNECT", "STORE_GET", "STORE_PUT", "LOG_FOLLOW", "ERR", "READY", "PONG", "MUSIC_OPEN", "MUSIC_STATE", "MUSIC_CLOSE"];
    const READS: [&str; 12] = ["WELCOME", "CONFIG", "LOG", "TIME", "ERR", "STORE_VALUE", "OK", "PING", "STOP", "MUSIC_POS", "MUSIC_CMD", "BUTTON"];

    #[test]
    fn vectors_written() {
        let (mut n, mut want) = (0, 0);
        for v in vectors().0 {
            if !WRITES.contains(&v.name.as_str()) { continue; }
            want += 1;
            let made = match v.name.as_str() {
                "HELLO" => {
                    let k: usize = v.num("scopes");
                    let sc: Vec<(String, u16)> = (0..k).map(|i| (v.text(&format!("scopes[{i}].name")), v.num(&format!("scopes[{i}].version")))).collect();
                    write_hello(v.id, v.num("sdk"), &sc.iter().map(|(n, x)| (n.as_str(), *x)).collect::<Vec<_>>()).unwrap()
                }
                "CONNECT" => write_connect(v.id, v.num("kind"), v.num("entry"), &v.text("host"), v.num("port")).unwrap(),
                "STORE_GET" => write_store_get(v.id, &v.text("key")).unwrap(),
                "STORE_PUT" => write_store_put(v.id, &v.text("key"), &v.bytes("value")).unwrap(),
                "LOG_FOLLOW" => write_log_follow(v.id, &v.text("from")).unwrap(),
                "ERR" => write_err(v.id, v.num("code")).unwrap(),
                "MUSIC_OPEN" => write_music_open(v.id, v.num("rate"), v.num("channels")),
                "MUSIC_STATE" => write_music_state(v.id, v.num("state"), &v.text("title"), &v.text("artist")).unwrap(),
                _ => write_empty(v.number, v.id),          // READY, PONG, MUSIC_CLOSE
            };
            assert_eq!(made, v.packet, "{} ({}): what this client writes", v.name, v.case);
            n += 1;
        }
        // (eight with fields: the sample and three edges each; READY, PONG and MUSIC_CLOSE have no fields: the sample alone)
        assert_eq!((n, want), (35, 35), "every message this client writes, each case of it");
    }

    #[test]
    fn vectors_read() {
        let mut n = 0;
        for v in vectors().0 {
            let (kind, id, body) = head(&v.packet).unwrap();
            assert_eq!((kind, id), (v.number, v.id), "{} ({}): the header", v.name, v.case);
            let what = format!("{} ({})", v.name, v.case);
            match v.name.as_str() {
                "WELCOME" => {
                    let w = read_welcome(body).unwrap();
                    let k: usize = v.num("scopes");
                    let scopes = (0..k).map(|i| (v.text(&format!("scopes[{i}].name")), v.num(&format!("scopes[{i}].version")))).collect();
                    assert_eq!(w, Welcome { clock_set: v.num("clock_set"), model: v.text("model"), node: v.text("node"),
                                            name: v.text("name"), zone: v.text("zone"), scopes }, "{what}");
                }
                "CONFIG" => {
                    let c = read_config(body).unwrap();
                    let k: usize = v.num("settings");
                    assert_eq!(c.len(), k, "{what}");
                    for (i, (name, value)) in c.iter().enumerate() {
                        assert_eq!((name.clone(), value.clone()), (v.text(&format!("settings[{i}].name")), v.text(&format!("settings[{i}].value"))), "{what}");
                    }
                }
                "LOG" => {
                    let (cur, gap, lines) = read_log(body).unwrap();
                    assert_eq!((cur, gap), (v.text("cursor"), v.num("gap")), "{what}");
                    let k: usize = v.num("lines");
                    assert_eq!(lines, (0..k).map(|i| v.text(&format!("lines[{i}].line"))).collect::<Vec<_>>(), "{what}");
                }
                "TIME" => assert_eq!(read_time(body).unwrap(), (v.num("clock_set"), v.text("zone")), "{what}"),
                "ERR" => assert_eq!(read_err(body).unwrap(), v.num::<u16>("code"), "{what}"),
                "STORE_VALUE" => assert_eq!(read_store_value(body).unwrap(), v.bytes("value"), "{what}"),
                "OK" | "PING" | "STOP" => assert!(read_empty(body).is_some(), "{what}"),
                "MUSIC_POS" => assert_eq!(read_music_pos(body).unwrap(), (v.num("frames"), v.num("at"), v.num("queued")), "{what}"),
                "MUSIC_CMD" => assert_eq!(read_music_cmd(body).unwrap(), (v.num("cmd"), v.num("why")), "{what}"),
                "BUTTON" => assert_eq!(read_button(body).unwrap(), (v.num("key"), v.num("how")), "{what}"),
                _ => continue,
            }
            n += 1;
        }
        assert_eq!(n, 39, "every message this client reads, each case of it");
    }

    /// what this client's reader makes of a packet: Some when it takes it
    fn read_any(p: &[u8]) -> Option<()> {
        let (kind, _, body) = head(p)?;
        match kind {
            WELCOME => read_welcome(body).map(|_| ()),
            CONFIG => read_config(body).map(|_| ()),
            LOG => read_log(body).map(|_| ()),
            TIME => read_time(body).map(|_| ()),
            ERR => read_err(body).map(|_| ()),
            STORE_VALUE => read_store_value(body).map(|_| ()),
            MUSIC_POS => read_music_pos(body).map(|_| ()),
            MUSIC_CMD => read_music_cmd(body).map(|_| ()),
            BUTTON => read_button(body).map(|_| ()),
            OK | PING | STOP => read_empty(body),
            _ => Some(()),
        }
    }

    #[test]
    fn vectors_fit() {
        // Every value of a later version of a message this client writes goes to a peer of an earlier version as the
        // vectors' `fit` line has it (hassmic's pm_fit; finding 4 of a review of hassmic 60a8589): its table here
        // names the same, and an `unsent` line of such a message fails (finding 1 of a review of hassmic 61e3d8e: only
        // a message marked drop, which hassmic sends, is ever left unsent).  A line of a message this client does not
        // write is not its to send.  (None on file yet: no version above 1
        // has brought a value of ERR_* or MUSIC_*)
        held_to_fits(fits(), ERR_LATER, MUSIC_LATER);
    }

    #[test]
    fn vectors_fit_and_unsent_read() {
        // The same reader and check on vectors of its own as a later hassmic's SDK would have them: MUSIC_STATE with a
        // value 5 of audio.music 2 to PAUSED (its `fit` line), held to a table that says the same, and one that says
        // otherwise caught; BUTTON with a key 4 of button 2 to none (`>-`, its `unsent` line: BUTTON is marked drop),
        // read and passed over (not this client's to send); and an `unsent` line of MUSIC_STATE, which no hassmic's
        // vectors can have (a state is never left unsent), fails
        let dir = std::env::temp_dir().join(format!("hmvec-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let pkt = |v: u8| { let mut p = vec![MUSIC_STATE as u8, 0, 0, 0, 0x47, 0, 1, 0, v]; p.extend([1, 0, b't', 1, 0, b'a']); p };
        let bpkt = |k: u8| vec![BUTTON as u8, 0, 0, 0, 0x78, 0, 1, 0, k, BUTTON_PRESS];
        let hx = |p: Vec<u8>| p.iter().map(|b| format!("{b:02x}")).collect::<String>();
        std::fs::write(dir.join("MUSIC_STATE.vec"), format!("message MUSIC_STATE 71\nid 65607\nstate u8 1\ntitle str 74\nartist str 61\npacket {}\nfit state:audio.music:1 {} {}\n",
                                                            hx(pkt(1)), hx(pkt(5)), hx(pkt(MUSIC_PAUSED)))).unwrap();
        std::fs::write(dir.join("BUTTON.vec"), format!("message BUTTON 120\nid 65656\nkey u8 1\nhow u8 1\npacket {}\nunsent key:button:1 {}\n", hx(bpkt(1)), hx(bpkt(4)))).unwrap();
        let (cases, _) = vectors_in(dir.to_str().unwrap(), false);
        let f = fits_in(dir.to_str().unwrap());
        std::fs::write(dir.join("MUSIC_STATE.vec"), format!("message MUSIC_STATE 71\nid 65607\nstate u8 1\ntitle str 74\nartist str 61\npacket {}\nunsent state:audio.music:1 {}\n", hx(pkt(1)), hx(pkt(4)))).unwrap();
        let g = fits_in(dir.to_str().unwrap());
        std::fs::remove_dir_all(&dir).unwrap();
        assert!(cases.len() == 2 && f.len() == 2 && f.iter().any(|x| x.0 == "MUSIC_STATE" && x.3.is_some()) && f.iter().any(|x| x.0 == "BUTTON" && x.3.is_none()), "a fit line and an unsent line read");
        let t: &[(u16, u16, u16)] = &[(5, 2, MUSIC_PAUSED as u16)];
        assert_eq!(held_to_fits(f.clone(), ERR_LATER, t), 1, "MUSIC_STATE's fit held; BUTTON's unsent line not this client's");
        let wrong: &[(u16, u16, u16)] = &[(5, 2, MUSIC_PLAYING as u16)];
        assert!(std::panic::catch_unwind(|| held_to_fits(f.clone(), ERR_LATER, wrong)).is_err(), "a table that says otherwise than the vectors: caught");
        assert!(std::panic::catch_unwind(|| held_to_fits(g.clone(), ERR_LATER, t)).is_err(), "an unsent line of a message this client writes: caught");
    }

    /// every fit and unsent line of a message this client writes held to its tables: how many
    fn held_to_fits(fits: Vec<(String, String, Vec<u8>, Option<Vec<u8>>)>, err_later: &[(u16, u16, u16)], music_later: &[(u16, u16, u16)]) -> usize {
        let mut n = 0;
        for (name, what, a, b) in fits {
            let parts: Vec<&str> = what.split(':').collect();
            let (path, low): (&str, u16) = (parts[0], parts[2].parse().unwrap());
            let (at, size, later) = match (name.as_str(), path) {
                ("ERR", "code") => (8, 2, err_later),
                ("MUSIC_STATE", "state") => (8, 1, music_later),
                _ => continue,
            };
            let val = |p: &[u8]| if size == 2 { u16::from_le_bytes([p[at], p[at + 1]]) } else { p[at] as u16 };
            match b {
                Some(b) => {
                    assert_eq!(fit(val(&a), low, later), val(&b), "{name} {what}: the fallback its table here names");
                    assert_eq!(a[..at], b[..at], "{name} {what}: only the value differs");
                }
                // (an `unsent` line of a message this client writes: none can be, PROTOCOL.md "Versions")
                None => panic!("{name} {what}: an `unsent` line of a message this client writes (only a message marked drop is ever left unsent)"),
            }
            n += 1;
        }
        n
    }

    #[test]
    fn fit_by_version() {
        // fit's walk on a table of its own: 9 came with version 3 (to 2's 7), 7 with version 2 (to 1's 3)
        let t: &[(u16, u16, u16)] = &[(9, 3, 7), (7, 2, 3)];
        assert_eq!((fit(9, 3, t), fit(9, 2, t), fit(9, 1, t), fit(7, 1, t), fit(3, 1, t), fit(42, 1, t)), (9, 7, 3, 3, 3, 42));
        assert_eq!((fit(ERR_UNKNOWN, 1, ERR_LATER), fit(MUSIC_PAUSED as u16, 1, MUSIC_LATER)), (ERR_UNKNOWN, MUSIC_PAUSED as u16));
        // what Client::music_state sends: with a table where 4 came with audio.music 2 (to PAUSED), hassmic speaking
        // audio.music 1 gets PAUSED, one speaking 2 the value itself (no value it writes is ever left unsent)
        let at = |v: u16| Info { clock_set: true, model: String::new(), node: String::new(), name: String::new(), zone: String::new(), scopes: vec![("core".into(), 1), ("audio.music".into(), v)] };
        let t: &[(u16, u16, u16)] = &[(4, 2, MUSIC_PAUSED as u16)];
        let state = |r: Result<Vec<u8>, ()>| r.map(|p| p[8]);
        assert_eq!(state(music_state_packet(&at(1), t, 4, "t", "a")), Ok(MUSIC_PAUSED));
        assert_eq!(state(music_state_packet(&at(2), t, 4, "t", "a")), Err(()), "(4 is no value this client writes: refused)");
        assert_eq!(state(music_state_packet(&at(1), t, MUSIC_PLAYING, "t", "a")), Ok(MUSIC_PLAYING));
        assert!(at(1).sends(MUSIC_STATE) && !at(0).sends(MUSIC_STATE) && at(1).sends(READY) && !at(1).sends(STORE_GET) && !at(1).sends(BUTTON), "what it writes, by the scopes spoken");
        assert!(ERR_NOT_SPOKEN > ERR_UNSET && !err_ok(ERR_NOT_SPOKEN), "(the client's own code, apart from every ERR_* of the wire)");
    }

    #[test]
    fn vectors_refused() {
        let (cases, refused) = vectors();
        let (mut read, mut written) = (0, 0);
        for r in &refused {
            if READS.contains(&r.name.as_str()) {
                assert!(read_any(&r.packet).is_none(), "{} {}: taken by this client's reader", r.name, r.what);
                read += 1;
            }
            // (short, truncated and trailing are of the packet, not of a value: every writer here writes the layout)
            if !WRITES.contains(&r.name.as_str()) || ["short", "truncated", "trailing"].contains(&r.what.as_str()) { continue; }
            // a writer of this client's, given a value the packet's field breaks its rule with: refused.  The field's
            // bound is its length in the `long` case
            let (how, field) = r.what.split_once(':').unwrap();
            // a value its enum does not have (PROTOCOL.md "Test vectors": enum:<path>): this client's writer refuses one
            if how == "enum" {
                let made = match (r.name.as_str(), field) {
                    ("CONNECT", "kind") => write_connect(1, CAP_BUTTON + 1, 0, "", 0),
                    ("ERR", "code") => write_err(1, 0).or(write_err(1, ERR_UNSET + 1)),
                    ("MUSIC_STATE", "state") => write_music_state(1, 0, "", "").or(write_music_state(1, MUSIC_STOPPED + 1, "", "")),
                    _ => panic!("{} {}: no writer's rule for it here", r.name, r.what),
                };
                assert!(made.is_none(), "{} {}: written by this client", r.name, r.what);
                written += 1;
                continue;
            }
            // a value of a later version of its scope than this client speaks (version 1 of every scope; PROTOCOL.md
            // "Test vectors": later:<path>:<scope>:<N>): its writer refuses it, and the client sends that value's
            // fallback instead (`fit`, its tables, which vectors_fit holds to the `fit` lines)
            if how == "later" {
                let path = field.split(':').next().unwrap();
                let made = match (r.name.as_str(), path) {
                    ("ERR", "code") => write_err(1, u16::from_le_bytes([r.packet[8], r.packet[9]])),
                    ("MUSIC_STATE", "state") => write_music_state(1, r.packet[8], "", ""),
                    _ => panic!("{} {}: no writer's rule for it here", r.name, r.what),
                };
                assert!(made.is_none(), "{} {}: written by this client", r.name, r.what);
                written += 1;
                continue;
            }
            if (r.name.as_str(), how, field) == ("HELLO", "count", "scopes") {
                // a list over its bound: the `full` case's count + 1
                let full = cases.iter().find(|v| v.name == "HELLO" && v.case == "full").unwrap();
                let over = vec![("s", 1u16); full.num::<usize>("scopes") + 1];
                assert!(write_hello(1, 1, &over).is_none(), "HELLO count:scopes: written by this client");
                written += 1;
                continue;
            }
            let long = cases.iter().find(|v| v.name == r.name && v.case == "long").unwrap();
            let bad = if how == "nul" { b"a\0b".to_vec() } else { vec![b'a'; long.bytes(field).len() + 1] };
            let text = String::from_utf8(bad.clone()).unwrap();
            let made = match (r.name.as_str(), field) {
                ("CONNECT", "host") => write_connect(1, CAP_NET_SERVER, 0, &text, 0),
                ("STORE_GET", "key") => write_store_get(1, &text),
                ("STORE_PUT", "key") => write_store_put(1, &text, b"v"),
                ("STORE_PUT", "value") => write_store_put(1, "k", &bad),
                ("LOG_FOLLOW", "from") => write_log_follow(1, &text),
                ("MUSIC_STATE", "title") => write_music_state(1, MUSIC_PLAYING, &text, ""),
                ("MUSIC_STATE", "artist") => write_music_state(1, MUSIC_PLAYING, "", &text),
                ("HELLO", "scopes[0].name") => write_hello(1, 1, &[(&text, 1)]),
                _ => panic!("{} {}: no writer's rule for it here", r.name, r.what),
            };
            assert!(made.is_none(), "{} {}: written by this client", r.name, r.what);
            written += 1;
        }
        assert_eq!(refused.iter().filter(|r| r.what == "short").count(), refused.iter().map(|r| r.number).collect::<std::collections::HashSet<_>>().len(), "a short packet for every message");
        assert_eq!(refused.iter().filter(|r| r.what == "trailing").count(), refused.iter().map(|r| r.number).collect::<std::collections::HashSet<_>>().len(), "bytes after the last field refused, for every message");
        assert!(read > 20 && written >= 9, "refusals: {read} read, {written} written");
    }

    #[test]
    fn by_hand() {
        // a str has no NUL; bytes may
        assert!(write_store_get(1, "a\0b").is_none());
        assert_eq!(write_store_put(2, "k", b"a\0b").unwrap(), [42, 0, 0, 0, 2, 0, 0, 0, 1, 0, b'k', 3, 0, b'a', 0, b'b']);
        // WELCOME: clock_set, model, node, name, zone, scopes [("core", 1)]; a field cut short; a byte after the last
        // field: malformed (no "later field" to skip: a packet is its layout at the version spoken)
        let mut b = vec![1, 0, 0, 0, 2, 0, b'D', b'3', 4, 0, b'e', b'c', b'h', b'o', 0, 0, 1, 0, b'Z', 1, 0, 4, 0, b'c', b'o', b'r', b'e', 1, 0];
        let w = read_welcome(&b).unwrap();
        assert_eq!((w.node.as_str(), w.scopes.clone()), ("echo", vec![("core".to_string(), 1)]));
        assert_eq!(welcome_scopes(&w), Some(vec![("core".to_string(), 1)]));
        assert!(read_welcome(&b[..b.len() - 1]).is_none());
        b.push(0xff);
        assert!(read_welcome(&b).is_none(), "a byte after the last field");
        // the scopes this client takes: its own, at its version or lower, each once, core among them
        let wl = |sc: &[(&str, u16)]| Welcome { clock_set: 0, model: String::new(), node: String::new(), name: String::new(), zone: String::new(),
                                                 scopes: sc.iter().map(|(n, v)| (n.to_string(), *v)).collect() };
        assert!(welcome_scopes(&wl(&[("core", 1), ("storage", 1), ("button", 1)])).is_some());
        assert!(welcome_scopes(&wl(&[("core", 2)])).is_none(), "a version over the client's");
        assert!(welcome_scopes(&wl(&[("core", 0)])).is_none(), "version 0");
        assert!(welcome_scopes(&wl(&[("core", 1), ("wake", 1)])).is_none(), "a scope this client does not have");
        assert!(welcome_scopes(&wl(&[("core", 1), ("storage", 1), ("storage", 1)])).is_none(), "a scope twice");
        assert!(welcome_scopes(&wl(&[("storage", 1)])).is_none(), "no core");
        // HELLO: this client's own table, each once
        let mut names: Vec<&str> = SCOPES.iter().map(|s| s.0).collect();
        names.sort(); names.dedup();
        assert_eq!(names.len(), SCOPES.len());
        assert!(SCOPES.iter().all(|s| s.1 == 1));
        // a type of a scope not spoken: not of what Info speaks
        let info = Info { clock_set: false, model: String::new(), node: String::new(), name: String::new(), zone: String::new(),
                          scopes: vec![("core".into(), 1), ("log.read.full".into(), 1)] };
        assert!(info.speaks(PING) && info.speaks(LOG) && !info.speaks(BUTTON) && !info.speaks(STORE_VALUE) && !info.speaks(MUSIC_CMD));
        assert!(info.granted("log.read.full") && !info.granted("button") && !info.granted("core"), "what was granted: the scopes spoken, core apart");
        // an enum's value this client does not speak: the packet malformed, never written
        assert_eq!(read_music_cmd(&[MCMD_STOP, 1]), Some((MCMD_STOP, 1)));
        assert!(read_music_cmd(&[MCMD_PREVIOUS + 1, 1]).is_none() && read_music_cmd(&[MCMD_STOP, 5]).is_none() && read_button(&[BUTTON_ACTION, 3]).is_none() && read_err(&[9, 0]).is_none());
        assert!(write_music_state(1, 4, "", "").is_none() && write_err(1, 0).is_none() && write_connect(1, 19, 0, "", 0).is_none() && write_connect(1, CAP_NET_LAN, 0, "1.2.3.4", 80).is_some());
        assert!(!info.speaks(9999), "a type this client does not read");
        let mut long = vec![0, 0, 0, 65, 0];
        long.extend(std::iter::repeat([0u8, 0]).take(65).flatten());
        assert!(read_log(&long).is_none());
        assert_eq!(head(&[51, 0, 0]), None);
    }

    // ---- the client's behaviour against a stand-in broker on a socketpair (what a review of hassmic 0dabfa6 found in
    // the C client, held here too): R2, R3, R4, R8, R9
    extern "C" { fn socketpair(d: i32, t: i32, p: i32, sv: *mut i32) -> i32; fn close(fd: i32) -> i32; }
    use std::sync::{Arc, Mutex};

    fn send(fd: i32, m: &[u8]) { sys::send_fd(fd, m, sys::MSG_NOSIGNAL).unwrap(); }
    fn recv(fd: i32, ms: i32) -> Option<(u16, u32, Vec<u8>)> {
        let mut pf = [sys::pollfd { fd, events: sys::POLLIN, revents: 0 }];
        if sys::poll_fds(&mut pf, ms) != Ok(1) { return None; }
        let mut b = vec![0u8; MAX_PACKET];
        let (n, _) = sys::recv_packet(fd, &mut b).ok()?;
        if n == 0 { return None; }
        let (k, id, body) = head(&b[..n])?;
        Some((k, id, body.to_vec()))
    }
    fn s(t: &str) -> Vec<u8> { let mut v = (t.len() as u16).to_le_bytes().to_vec(); v.extend_from_slice(t.as_bytes()); v }
    fn log_msg(id: u32, line: &str) -> Vec<u8> {
        let mut w = W::new(LOG, id); w.0.extend(s("0000000000000000:1")); w.0.push(0); w.0.extend(1u16.to_le_bytes()); w.0.extend(s(line)); w.0
    }

    type Said = Arc<Mutex<Vec<String>>>;
    struct Stand { said: Said, kind: &'static str, ticks: u32 }
    impl Plugin for Stand {
        fn log(&mut self, c: &mut Client, _cur: &str, _gap: bool, lines: &[String]) {
            for l in lines { self.said.lock().unwrap().push(format!("got {l}")); }
            if self.kind == "r3" && lines.first().map(|l| l == "L0").unwrap_or(false) { let _ = c.store_get("k"); }
        }
        fn tick(&mut self, c: &mut Client) {
            self.ticks += 1;
            if self.ticks > 1 { return; }
            let r = match self.kind {
                "r2" => format!("connect {:?}", c.connect(0, 0).map(|_| ())),
                "r4" => { let _ = c.store_put("other", b"A"); format!("put {:?}", c.store_put("cursor", b"A")) }
                "r8" => format!("follow {:?}", c.log_follow("0000000000000000:999")),
                // (audio.music not spoken: start()'s WELCOME has no such scope)
                "ns" => format!("music {:?} state {}", c.music_open(16000, 1), c.music_state(MUSIC_PLAYING, "t", "a")),
                _ => return,
            };
            self.said.lock().unwrap().push(r);
        }
        fn button(&mut self, _c: &mut Client, key: u8, how: u8) { self.said.lock().unwrap().push(format!("button {key} {how}")); }
        fn stop(&mut self, c: &mut Client) {
            let r = if self.kind == "r4" { format!(" put {:?}", c.store_put("cursor", b"B-at-stop")) } else { String::new() };
            self.said.lock().unwrap().push(format!("stop{r}"));
        }
    }

    /// WELCOME for request ID, speaking SCOPES
    fn welcome(id: u32, scopes: &[(&str, u16)]) -> Vec<u8> {
        let mut w = W::new(WELCOME, id); w.u32(1); for t in ["pc", "n", "n", "UTC"] { w.0.extend(s(t)); }
        w.u16(scopes.len() as u16); for (n, v) in scopes { w.0.extend(s(n)); w.u16(*v); }
        w.0
    }
    /// the plugin in a thread, past its start: the broker's end, the plugin's result, what it said
    fn start(kind: &'static str) -> (i32, std::thread::JoinHandle<i32>, Said) {
        let mut sv = [0i32; 2];
        assert_eq!(unsafe { socketpair(1, 5, 0, sv.as_mut_ptr()) }, 0);       // AF_UNIX, SOCK_SEQPACKET
        let said: Said = Arc::new(Mutex::new(Vec::new()));
        let (b, pfd, s2) = (sv[0], sv[1], said.clone());
        let t = std::thread::spawn(move || { let mut p = Stand { said: s2, kind, ticks: 0 }; let r = run_on(pfd, &mut p); unsafe { close(pfd); } r });
        let (k, id, _) = recv(b, 3000).unwrap(); assert_eq!(k, HELLO);
        send(b, &welcome(id, &[("core", 1), ("storage", 1), ("log.read", 1), ("net.server", 1), ("button", 1)]));
        send(b, &W::new(CONFIG, 0).u16(0).0);
        assert_eq!(recv(b, 3000).unwrap().0, READY);
        (b, t, said)
    }
    /// STOP, then what comes answered (a storage write OK), counted by type, until the plugin ended
    fn stop_and_wait(b: i32, t: std::thread::JoinHandle<i32>, ms: u64) -> (Option<i32>, HashMap<u16, u32>) {
        let mut seen = HashMap::new();
        send(b, &W::new(STOP, 0).0);
        let end = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < end && !t.is_finished() {
            if let Some((k, id, _)) = recv(b, 20) {
                *seen.entry(k).or_insert(0) += 1;
                if k == STORE_PUT { send(b, &W::new(OK, id).0); }
            }
        }
        let r = if t.is_finished() { Some(t.join().unwrap()) } else { None };
        unsafe { close(b); }
        (r, seen)
    }

    #[test]
    fn not_spoken_refused_here() {
        // a request of a scope its WELCOME does not speak (audio.music): refused by the client itself, ERR_NOT_SPOKEN,
        // apart from ERR_BAD, and nothing goes to hassmic (a review of hassmic 61e3d8e, finding 1)
        let (b, t, said) = start("ns");
        let mut seen = Vec::new();
        while let Some((k, _, _)) = recv(b, 500) { seen.push(k); }
        let (r, _) = stop_and_wait(b, t, 3000);
        let got: Vec<String> = said.lock().unwrap().iter().filter(|l| l.starts_with("music")).cloned().collect();
        assert_eq!(got, [format!("music Err({ERR_NOT_SPOKEN}) state false")], "music_open: ERR_NOT_SPOKEN; music_state: not sent");
        assert!(!seen.contains(&MUSIC_OPEN) && !seen.contains(&MUSIC_STATE), "nothing of audio.music reached hassmic: {seen:?}");
        assert_eq!(r, Some(0));
    }

    #[test]
    fn r3_r9_kept_first_and_each_log_answered() {
        let (b, t, said) = start("r3");
        send(b, &log_msg(1000, "L0"));
        let gid = loop { let (k, id, _) = recv(b, 3000).unwrap(); if k == STORE_GET { break id; } };
        send(b, &log_msg(1001, "L1"));
        send(b, &W::new(STORE_VALUE, gid).bytes(b"x", 16384).unwrap().0);
        send(b, &log_msg(1002, "L2"));
        let mut oks = Vec::new();
        while let Some((k, id, _)) = recv(b, 500) { if k == OK { oks.push(id); } }
        let (r, _) = stop_and_wait(b, t, 3000);
        let got: Vec<String> = said.lock().unwrap().iter().filter(|l| l.starts_with("got")).cloned().collect();
        assert_eq!(got, ["got L0", "got L1", "got L2"], "R3: kept before newer");
        assert_eq!(oks, [1000, 1001, 1002], "R9: each LOG answered OK, its own id, in order");
        assert_eq!(r, Some(0));
    }

    /// the plugin in a thread before its start: the broker's end, the plugin's result, what it said, HELLO's id and body
    fn begin(kind: &'static str) -> (i32, std::thread::JoinHandle<i32>, Said, u32, Vec<u8>) {
        let mut sv = [0i32; 2];
        assert_eq!(unsafe { socketpair(1, 5, 0, sv.as_mut_ptr()) }, 0);
        let said: Said = Arc::new(Mutex::new(Vec::new()));
        let (b, pfd, s2) = (sv[0], sv[1], said.clone());
        let t = std::thread::spawn(move || { let mut p = Stand { said: s2, kind, ticks: 0 }; let r = run_on(pfd, &mut p); unsafe { close(pfd); } r });
        let (k, id, body) = recv(b, 3000).unwrap(); assert_eq!(k, HELLO);
        (b, t, said, id, body)
    }

    /// the plugin's result once it ended within MS, None while it runs (the test fails; closing the broker's end ends it)
    fn join_in(t: std::thread::JoinHandle<i32>, ms: u64) -> Option<i32> {
        let end = Instant::now() + Duration::from_millis(ms);
        while !t.is_finished() && Instant::now() < end { std::thread::sleep(Duration::from_millis(10)); }
        if t.is_finished() { Some(t.join().unwrap()) } else { None }
    }

    #[test]
    fn scopes_at_the_start() {
        // HELLO offers this client's table, each at its version
        let (b, t, _, id, body) = begin("-");
        let mut r = R::new(&body);
        assert_eq!(r.u32(), Some(CLIENT_VERSION));
        let n = r.u16().unwrap() as usize;
        let offered: Vec<(String, u16)> = (0..n).map(|_| (r.str(24).unwrap(), r.u16().unwrap())).collect();
        assert!(r.end().is_some());
        assert_eq!(offered, SCOPES.iter().map(|(n, v)| (n.to_string(), *v)).collect::<Vec<_>>());
        // a WELCOME that speaks what the client does not have: it ends, nothing sent
        send(b, &welcome(id, &[("core", 1), ("wake", 1)]));
        assert_eq!(join_in(t, 3000), Some(1), "a scope it does not have");
        unsafe { close(b); }
        for bad in [vec![("core", 2u16)], vec![("storage", 1)], vec![("core", 1), ("core", 1)]] {
            let (b, t, _, id, _) = begin("-");
            send(b, &welcome(id, &bad));
            assert_eq!(join_in(t, 3000), Some(1), "{bad:?}");
            unsafe { close(b); }
        }
        // a WELCOME with a byte after its scopes: malformed, the start refused
        let (b, t, _, id, _) = begin("-");
        let mut w = welcome(id, &[("core", 1)]); w.push(0);
        send(b, &w);
        assert_eq!(join_in(t, 3000), Some(1), "trailing");
        unsafe { close(b); }
    }

    #[test]
    fn unspoken_and_malformed_not_acted_on() {
        // button not spoken: its BUTTON reaches no callback (an id: answered as a type it does not read)
        let (b, t, said, id, _) = begin("-");
        send(b, &welcome(id, &[("core", 1), ("storage", 1)]));
        send(b, &W::new(CONFIG, 0).u16(0).0);
        assert_eq!(recv(b, 3000).unwrap().0, READY);
        send(b, &W::new(BUTTON, 5).u8(BUTTON_ACTION).u8(BUTTON_PRESS).0);
        assert_eq!(recv(b, 3000).map(|(k, i, body)| (k, i, read_err(&body))), Some((ERR, 5, Some(ERR_UNKNOWN))), "BUTTON of a scope not spoken");
        // a PING with a byte after it: not answered; one without: PONG
        send(b, &W::new(PING, 6).u8(0).0);
        send(b, &W::new(PING, 7).0);
        assert_eq!(recv(b, 3000).map(|(k, i, _)| (k, i)), Some((PONG, 7)), "the malformed PING unanswered");
        // a STOP with a byte after it: not a stop
        send(b, &W::new(STOP, 0).u8(0).0);
        std::thread::sleep(Duration::from_millis(200));
        assert!(!t.is_finished(), "a malformed STOP ended it");
        let (r, _) = stop_and_wait(b, t, 3000);
        assert_eq!(r, Some(0));
        assert!(!said.lock().unwrap().iter().any(|l| l.starts_with("button")), "{:?}", said.lock().unwrap());
        assert_eq!(said.lock().unwrap().iter().filter(|l| *l == "stop").count(), 1);
    }

    #[test]
    fn malformed_answers_and_stop_while_a_request_waits() {
        // a STOP with a byte after it while a CONNECT waits: not a stop, the wait goes on to its answer
        let (b, t, said) = start("r2");
        let cid = loop { let (k, id, _) = recv(b, 3000).unwrap(); if k == CONNECT { break id; } };
        send(b, &W::new(STOP, 0).u8(0).0);
        std::thread::sleep(Duration::from_millis(200));
        assert!(said.lock().unwrap().is_empty(), "the malformed STOP ended the wait: {:?}", said.lock().unwrap());
        send(b, &write_err(cid, ERR_BUSY).unwrap());
        let (r, _) = stop_and_wait(b, t, 3000);
        assert_eq!(r, Some(0));
        assert_eq!(*said.lock().unwrap(), [format!("connect Err({ERR_BUSY})"), "stop".to_string()]);
        // an OK with a body: malformed, the request refused (ERR_BAD), never taken as its answer
        let (b, t, said) = start("r2");
        let cid = loop { let (k, id, _) = recv(b, 3000).unwrap(); if k == CONNECT { break id; } };
        send(b, &W::new(OK, cid).u8(0).0);
        std::thread::sleep(Duration::from_millis(200));
        let (r, _) = stop_and_wait(b, t, 3000);
        assert_eq!(r, Some(0));
        assert_eq!(said.lock().unwrap().first(), Some(&format!("connect Err({ERR_BAD})")));
    }

    #[test]
    fn r2_stop_while_a_connect_waits() {
        let (b, t, said) = start("r2");
        loop { let (k, _, _) = recv(b, 3000).unwrap(); if k == CONNECT { break; } }
        let (r, _) = stop_and_wait(b, t, 1500);
        assert_eq!(r, Some(0), "R2: it ends by itself");
        assert_eq!(*said.lock().unwrap(), [format!("connect Err({ERR_FAILED})"), "stop".to_string()], "R2: FAILED, then its stop");
    }

    #[test]
    fn r4_writes_at_and_after_stop() {
        let (b, t, said) = start("r4");
        let mut n = 0;
        while n < 2 { let (k, id, _) = recv(b, 3000).unwrap(); if k == STORE_PUT { send(b, &write_err(id, ERR_BUSY).unwrap()); n += 1; } }
        std::thread::sleep(Duration::from_millis(200));
        let (r, seen) = stop_and_wait(b, t, 3000);
        assert_eq!(r, Some(0));
        assert_eq!(seen.get(&STORE_PUT), Some(&2), "R4: the stop's write and the one kept for later: both after STOP");
        assert!(said.lock().unwrap().contains(&"stop put Ok(true)".to_string()), "R4: the write in stop goes at once: {:?}", said.lock().unwrap());
    }

    #[test]
    fn r8_a_refused_follow_keeps_the_old_place() {
        let (b, t, said) = start("r8");
        let fid = loop { let (k, id, _) = recv(b, 3000).unwrap(); if k == LOG_FOLLOW { break id; } };
        send(b, &log_msg(7, "L7"));
        send(b, &write_err(fid, ERR_BAD).unwrap());
        send(b, &log_msg(8, "L8"));
        std::thread::sleep(Duration::from_millis(300));
        let (r, _) = stop_and_wait(b, t, 3000);
        assert_eq!(r, Some(0));
        let said = said.lock().unwrap();
        assert!(said.contains(&format!("follow Err({ERR_BAD})")), "{said:?}");
        let got: Vec<&String> = said.iter().filter(|l| l.starts_with("got")).collect();
        assert_eq!(got, ["got L7", "got L8"], "R8: the old place's LOG handed on, nothing lost");
    }
}
