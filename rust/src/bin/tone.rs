//! The tone example in Rust: tone/main.c's behaviour, rule for rule, on the template's own client (client.rs), which
//! speaks hassmic's plugin protocol from sdk/PROTOCOL.md without the C SDK.  tone/main.c says why each rule is so;
//! the comments here only say where Rust differs.  Its id is the C build's with "-rs" (tools/rsmanifest.py).
//! 0BSD, as the rest of the template's own code (LICENSE).
#[path = "../client.rs"]
mod client;
#[path = "../sys.rs"]
mod sys;

use client::{log, Client, Plugin, MCMD_PAUSE, MCMD_RESUME, MCMD_STOP, MUSIC_PAUSED, MUSIC_PLAYING, MUSIC_STOPPED};
use std::f64::consts::PI;

const RATE: u32 = 48000;
const CHANNELS: u8 = 2;

struct Tone { freq: u32, level_db: i32, amp: f64, phase: f64, stream: Option<i32>, state: u8, opened: bool, logged: u64 }

impl Tone {
    fn say(&mut self, c: &mut Client, st: u8) {
        self.state = st;
        c.music_state(st, "A tone", "hassmic's plugin template");
        log(match st { MUSIC_PLAYING => "playing", MUSIC_PAUSED => "paused", _ => "stopped" });
    }
    fn open(&mut self, c: &mut Client) {
        match c.music_open(RATE, CHANNELS) {
            Ok(fd) => { self.stream = Some(fd); self.say(c, MUSIC_PLAYING); }
            Err(why) => log(&format!("no stream (error {why})")),
        }
    }
    fn close(&mut self, c: &mut Client) { if self.stream.take().is_some() { let _ = c.music_close(); } }
}

impl Plugin for Tone {
    fn config(&mut self, _c: &mut Client, settings: &[(String, String)]) {
        for (k, v) in settings {
            match k.as_str() {
                "frequency" => self.freq = v.parse().unwrap_or(440),
                "level" => self.level_db = v.parse().unwrap_or(-20),
                _ => {}
            }
        }
        if !(50..=4000).contains(&self.freq) { self.freq = 440; }
        if !(-60..=-6).contains(&self.level_db) { self.level_db = -20; }
        self.amp = 32767.0 * 10f64.powf(self.level_db as f64 / 20.0);
        log(&format!("a tone of {} Hz at {} dB", self.freq, self.level_db));
    }

    fn tick(&mut self, c: &mut Client) { if !self.opened { self.opened = true; self.open(c); } }

    fn stop(&mut self, c: &mut Client) { self.close(c); }

    fn music_writable(&mut self, c: &mut Client, fd: i32) {
        let frames = (RATE / 100) as usize;
        let step = 2.0 * PI * self.freq as f64 / RATE as f64;
        let mut b = vec![0u8; frames * CHANNELS as usize * 2];
        loop {
            let mut p = self.phase;
            for i in 0..frames {
                let v = ((self.amp * p.sin()) as i16).to_le_bytes();
                b[4 * i..4 * i + 2].copy_from_slice(&v); b[4 * i + 2..4 * i + 4].copy_from_slice(&v);
                p += step;
            }
            match sys::write_fd(fd, &b) {
                Ok(0) | Err(sys::EAGAIN) | Err(sys::EINTR) => return,
                Err(e) => {
                    // the stream is gone: another plugin took the speaker, or hassmic ended it
                    log(&format!("the stream ended ({})", std::io::Error::from_raw_os_error(e)));
                    self.close(c); self.say(c, MUSIC_STOPPED);
                    return;
                }
                Ok(w) => {
                    self.phase = (self.phase + step * (w / (2 * CHANNELS as usize)) as f64) % (2.0 * PI);
                    if w < b.len() { return; }
                }
            }
        }
    }

    fn music_cmd(&mut self, c: &mut Client, cmd: u8, why: u8) {
        let whys = ["?", "the action button", "the stop word", "another source", "Home Assistant"];
        let what = match cmd { MCMD_PAUSE => "pause", MCMD_RESUME => "resume", MCMD_STOP => "stop", _ => "skip" };
        log(&format!("asked to {what} by {}", whys[if (1..=4).contains(&why) { why as usize } else { 0 }]));
        match cmd {
            MCMD_PAUSE => if self.state == MUSIC_PLAYING { self.say(c, MUSIC_PAUSED); },
            MCMD_RESUME => if self.stream.is_none() { self.open(c) } else { self.say(c, MUSIC_PLAYING) },
            MCMD_STOP => { self.close(c); self.say(c, MUSIC_STOPPED); }
            _ => {}         // a tone has no next one
        }
    }

    fn music_pos(&mut self, _c: &mut Client, frames: u64, _at_us: u64, queued_us: u32) {
        if frames / RATE as u64 >= self.logged + 60 { self.logged = frames / RATE as u64; log(&format!("{} s played ({} ms queued)", self.logged, queued_us / 1000)); }
    }
}

fn main() {
    let mut t = Tone { freq: 440, level_db: -20, amp: 0.0, phase: 0.0, stream: None, state: MUSIC_STOPPED, opened: false, logged: 0 };
    std::process::exit(client::run(&mut t));
}
