//! The example plugin in Rust: src/main.c's behaviour, rule for rule, on a client of its own (client.rs) that speaks
//! hassmic's plugin protocol from sdk/PROTOCOL.md, without the C SDK.  src/main.c says why each rule is so; the
//! comments here only say where Rust differs.  Optional: C on the SDK's client is the main path (README.md "Rust").
//!
//! Its id is the C build's with "-rs" (tools/rsmanifest.py), so that an Echo can have both; every setting, capability
//! and limit is the C build's manifest's.  0BSD, as the rest of the template's own code (LICENSE).
mod client;
mod sys;

use client::{log, Client, Plugin};
use std::os::fd::{AsRawFd, OwnedFd};
use std::time::{Duration, Instant};

const KEY_STARTS: &str = "starts";
const KEY_SENT: &str = "sent";
const SAVE: Duration = Duration::from_secs(600);
const RETRY_MIN: Duration = Duration::from_secs(5);
const RETRY_MAX: Duration = Duration::from_secs(300);
/// the protocol select's options, in the manifest's order: also the net.server entries' (0 UDP, 1 TCP)
const PROTOS: [&str; 2] = ["UDP", "TCP"];

#[derive(Clone)]
struct Cfg { greeting: String, interval: u64, proto: u8, verbose: bool, server: String }

struct Example {
    cfg: Cfg,
    starts: u64,
    sent: u64,
    sent_saved: Option<u64>,
    saved_at: Instant,
    sock: Option<OwnedFd>,
    sock_proto: u8,
    next_greeting: Option<Instant>,
    retry_at: Option<Instant>,
    backoff: Option<Duration>,
    last_why: Option<u16>,
    unset: bool,
}

fn why_text(why: u16) -> &'static str {
    match why {
        client::ERR_UNSET => "no server or no port entered",
        client::ERR_NOT_FOUND => "its name has no address",
        client::ERR_FAILED => "it did not answer",
        client::ERR_DENIED => "hassmic does not let plugins reach that address",
        client::ERR_BUSY => "too many connects in the last minute",
        _ => "refused",
    }
}

fn errtext(e: i32) -> String { std::io::Error::from_raw_os_error(e).to_string() }

impl Example {
    fn load_number(c: &mut Client, key: &str) -> u64 {
        match c.store_get(key) {
            Ok(None) => 0,
            Ok(Some(v)) => std::str::from_utf8(&v).ok().and_then(|s| s.parse().ok()).unwrap_or(0),
            Err(_) => { log(&format!("could not read \"{key}\" from storage: starting from 0")); 0 }
        }
    }

    /// store_put: Ok(true) written, Ok(false) kept by the client and written later, Err(ERR_BUSY) neither
    fn save_number(c: &mut Client, key: &str, v: u64) -> bool {
        match c.store_put(key, v.to_string().as_bytes()) {
            Ok(_) => true,
            Err(client::ERR_BUSY) => { log(&format!("\"{key}\" not stored: the Echo took too many writes of this plugin's lately")); false }
            Err(why) => { log(&format!("\"{key}\" not stored (error {why})")); false }
        }
    }

    fn save_sent(&mut self, c: &mut Client) {
        if self.sent_saved == Some(self.sent) { return; }
        if Self::save_number(c, KEY_SENT, self.sent) { self.sent_saved = Some(self.sent); }
        self.saved_at = Instant::now();
    }

    fn close_sock(&mut self, why: Option<&str>) {
        if self.sock.take().is_none() { return; } // (dropping the OwnedFd closes it)
        if let Some(why) = why {
            log(&format!("lost the server: {why}"));
            self.backoff = Some(RETRY_MIN);
            self.retry_at = Some(Instant::now() + RETRY_MIN);
        }
    }

    fn open_sock(&mut self, c: &mut Client) {
        match c.connect(self.cfg.proto, 0) {
            Err(why) => {
                if self.last_why != Some(why) { log(&format!("cannot reach the server: {}", why_text(why))); }
                self.last_why = Some(why);
                if why == client::ERR_UNSET { self.unset = true; return; }
                let b = self.backoff.map_or(RETRY_MIN, |b| (b * 2).min(RETRY_MAX));
                self.backoff = Some(b);
                self.retry_at = Some(Instant::now() + b);
            }
            Ok(fd) => {
                self.sock = Some(fd);
                self.sock_proto = self.cfg.proto;
                self.backoff = None;
                self.last_why = None;
                self.next_greeting = None;
                log(&format!("connected to the server ({})", PROTOS[self.sock_proto as usize]));
            }
        }
    }

    fn fd(&self) -> i32 { self.sock.as_ref().map_or(-1, |s| s.as_raw_fd()) }

    fn peer_gone(&self) -> bool {
        let mut p = [sys::pollfd { fd: self.fd(), events: sys::POLLIN, revents: 0 }];
        if sys::poll_fds(&mut p, 0) != Ok(1) { return false; }
        if p[0].revents & (sys::POLLERR | sys::POLLHUP) != 0 { return true; }
        let mut b = [0u8; 256];
        loop {
            match sys::recv_fd(self.fd(), &mut b, sys::MSG_DONTWAIT) {
                Ok(0) => return true,
                Ok(_) => continue,
                Err(e) => return e != sys::EAGAIN && e != sys::EINTR,
            }
        }
    }

    fn greet(&mut self, c: &Client) {
        let tcp = self.sock_proto == 1;
        let msg = format!("{} from {} (start {}, greeting {}){}", self.cfg.greeting, c.info.node, self.starts, self.sent + 1, if tcp { "\n" } else { "" });
        match sys::send_fd(self.fd(), msg.as_bytes(), sys::MSG_DONTWAIT | sys::MSG_NOSIGNAL) {
            Ok(n) if n == msg.len() => {
                self.sent += 1;
                if self.cfg.verbose { log(&format!("greeted the server: {}", msg.trim_end())); }
            }
            Err(e) if e == sys::EAGAIN || e == sys::EINTR => {}
            Err(e) if e == sys::ECONNREFUSED && !tcp => {}
            Ok(_) => self.close_sock(Some("only part of a greeting was written")),
            Err(e) => self.close_sock(Some(&errtext(e))),
        }
    }
}

impl Plugin for Example {
    fn config(&mut self, _c: &mut Client, settings: &[(String, String)]) {
        let mut n = self.cfg.clone();
        for (k, v) in settings {
            match k.as_str() {
                "greeting" => n.greeting = if v.is_empty() { "hello".into() } else { v.clone() },
                "interval" => n.interval = v.parse::<u64>().ok().filter(|x| *x >= 1).map_or(60, |x| x.min(3600)),
                "protocol" => n.proto = PROTOS.iter().position(|p| p == v).unwrap_or(0) as u8,
                "verbose" => n.verbose = v == "1",
                "server" => n.server = v.clone(),
                _ => {}
            }
        }
        if self.sock.is_some() && n.proto != self.sock_proto { self.close_sock(None); self.retry_at = None; self.backoff = None; }
        if n.interval != self.cfg.interval { self.next_greeting = None; }
        self.unset = false;
        self.cfg = n;
    }

    fn ready(&mut self, c: &mut Client) {
        self.starts = Self::load_number(c, KEY_STARTS) + 1;
        self.sent = Self::load_number(c, KEY_SENT);
        self.sent_saved = Some(self.sent);
        Self::save_number(c, KEY_STARTS, self.starts);
        self.saved_at = Instant::now();
        let s = |n: u64| if n == 1 { "" } else { "s" };
        log(&format!("started {} time{}, {} greeting{} sent before; {}", self.starts, s(self.starts), self.sent, s(self.sent),
                     if self.cfg.server.is_empty() { "no server entered yet: nothing is sent" } else { "connects at its first tick" }));
    }

    fn tick(&mut self, c: &mut Client) {
        let now = Instant::now();
        if self.sock.is_some() && self.sock_proto == 1 && self.peer_gone() { self.close_sock(Some("it closed the connection")); }
        if self.sock.is_none() && !self.cfg.server.is_empty() && !self.unset && self.retry_at.map_or(true, |t| now >= t) { self.open_sock(c); }
        if self.sock.is_some() && self.next_greeting.map_or(true, |t| now >= t) {
            self.greet(c);
            self.next_greeting = Some(now + Duration::from_secs(self.cfg.interval));
        }
        if now.duration_since(self.saved_at) >= SAVE { self.save_sent(c); }
    }

    fn button(&mut self, _c: &mut Client, key: u8, how: u8) {
        if key != client::BUTTON_ACTION || how != client::BUTTON_HOLD { return; }
        log(&format!("the action button held: greeting {}", if self.sock.is_some() { "now" } else { "once connected" }));
        self.next_greeting = None;
    }

    fn stop(&mut self, c: &mut Client) {
        self.sent_saved = None;
        self.save_sent(c);
        self.close_sock(None);
    }
}

fn main() {
    let mut p = Example {
        cfg: Cfg { greeting: "hello".into(), interval: 60, proto: 0, verbose: false, server: String::new() },
        starts: 0, sent: 0, sent_saved: None, saved_at: Instant::now(), sock: None, sock_proto: 0, next_greeting: None,
        retry_at: None, backoff: None, last_why: None, unset: false,
    };
    std::process::exit(client::run(&mut p));
}
