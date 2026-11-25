//! The few functions of musl that std does not offer, declared by hand: Rust's musl targets link musl statically
//! anyway (std is built on it), so these are no dependency.  Layouts are musl's, for 32-bit ARM and x86-64 alike
//! (each field noted where the two differ).  Constants are Linux's, the same on both.
#![allow(non_camel_case_types, dead_code)]
use std::os::raw::{c_int, c_long, c_void};

pub const MSG_DONTWAIT: c_int = 0x40;
pub const MSG_NOSIGNAL: c_int = 0x4000;
pub const POLLIN: i16 = 1;
pub const POLLOUT: i16 = 4;
pub const POLLERR: i16 = 8;
pub const POLLHUP: i16 = 16;
pub const SOL_SOCKET: c_int = 1;
pub const SCM_RIGHTS: c_int = 1;
pub const SO_KEEPALIVE: c_int = 9;
pub const IPPROTO_TCP: c_int = 6;
pub const TCP_KEEPIDLE: c_int = 4;
pub const TCP_KEEPINTVL: c_int = 5;
pub const TCP_KEEPCNT: c_int = 6;
pub const EINTR: i32 = 4;
pub const EAGAIN: i32 = 11;
pub const ENOBUFS: i32 = 105;
pub const ECONNREFUSED: i32 = 111;
const SIGSYS: c_int = 31;
const SA_SIGINFO: c_int = 4;

#[repr(C)]
pub struct pollfd {
    pub fd: c_int,
    pub events: i16,
    pub revents: i16,
}

#[repr(C)]
struct iovec {
    base: *mut c_void,
    len: usize,
}

/// musl's struct msghdr: on 64-bit its int msg_iovlen and socklen_t msg_controllen sit each with a zero pad after
/// them, which on a little-endian machine is a usize; on 32-bit every field is 4 bytes
#[repr(C)]
struct msghdr {
    name: *mut c_void,
    namelen: u32,
    iov: *mut iovec,
    iovlen: usize,
    control: *mut c_void,
    controllen: usize,
    flags: c_int,
}

/// musl's struct sigaction: the handler, a 128-byte mask, the flags, the restorer
#[repr(C)]
struct sigaction_t {
    handler: usize,
    mask: [u32; 32],
    flags: c_int,
    restorer: usize,
}

extern "C" {
    fn recvmsg(fd: c_int, msg: *mut msghdr, flags: c_int) -> isize;
    fn send(fd: c_int, buf: *const c_void, len: usize, flags: c_int) -> isize;
    fn recv(fd: c_int, buf: *mut c_void, len: usize, flags: c_int) -> isize;
    fn poll(fds: *mut pollfd, n: std::os::raw::c_ulong, timeout: c_int) -> c_int;
    fn setsockopt(fd: c_int, level: c_int, name: c_int, val: *const c_void, len: u32) -> c_int;
    fn write(fd: c_int, buf: *const c_void, len: usize) -> isize;
    fn sigaction(sig: c_int, act: *const sigaction_t, old: *mut sigaction_t) -> c_int;
    fn syscall(n: c_long, ...) -> c_long;
}

fn errno() -> i32 {
    std::io::Error::last_os_error().raw_os_error().unwrap_or(0)
}

/// a byte count, or Err(errno)
pub fn send_fd(fd: c_int, b: &[u8], flags: c_int) -> Result<usize, i32> {
    let n = unsafe { send(fd, b.as_ptr() as *const c_void, b.len(), flags) };
    if n < 0 { Err(errno()) } else { Ok(n as usize) }
}

pub fn recv_fd(fd: c_int, b: &mut [u8], flags: c_int) -> Result<usize, i32> {
    let n = unsafe { recv(fd, b.as_mut_ptr() as *mut c_void, b.len(), flags) };
    if n < 0 { Err(errno()) } else { Ok(n as usize) }
}

/// poll's revents per descriptor, or Err(errno)
/// write(2) on a descriptor (a music stream): the bytes taken, or errno ("would block" is EAGAIN)
pub fn write_fd(fd: c_int, b: &[u8]) -> Result<usize, i32> {
    let n = unsafe { write(fd, b.as_ptr() as *const c_void, b.len()) };
    if n < 0 { Err(errno()) } else { Ok(n as usize) }
}

pub fn poll_fds(fds: &mut [pollfd], timeout_ms: i32) -> Result<i32, i32> {
    let n = unsafe { poll(fds.as_mut_ptr(), fds.len() as _, timeout_ms) };
    if n < 0 { Err(errno()) } else { Ok(n) }
}

pub fn set_int(fd: c_int, level: c_int, name: c_int, v: c_int) {
    unsafe { setsockopt(fd, level, name, &v as *const c_int as *const c_void, 4) };
}

/// one packet of a SOCK_SEQPACKET socket and the descriptor that came with it (SCM_RIGHTS): Ok(0) at its end
pub fn recv_packet(fd: c_int, buf: &mut [u8]) -> Result<(usize, Option<c_int>), i32> {
    // room for one cmsghdr and an int, aligned as the kernel aligns it (to a long)
    let mut control = [0usize; 8];
    let mut iov = iovec { base: buf.as_mut_ptr() as *mut c_void, len: buf.len() };
    let mut h = msghdr {
        name: std::ptr::null_mut(),
        namelen: 0,
        iov: &mut iov,
        iovlen: 1,
        control: control.as_mut_ptr() as *mut c_void,
        controllen: std::mem::size_of_val(&control),
        flags: 0,
    };
    let n = loop {
        let n = unsafe { recvmsg(fd, &mut h, 0) };
        if n >= 0 { break n as usize; }
        let e = errno();
        if e != EINTR { return Err(e); }
    };
    // cmsghdr: a size_t length, int level, int type, then the data at the header's size rounded up to a long
    let bytes: &[u8] = unsafe { std::slice::from_raw_parts(control.as_ptr() as *const u8, std::mem::size_of_val(&control)) };
    let w = std::mem::size_of::<usize>();
    let hdr = (w + 8 + w - 1) / w * w;
    let mut got = None;
    let mut off = 0usize;
    let end = h.controllen.min(bytes.len());
    while off + hdr <= end {
        let len = usize::from_ne_bytes(bytes[off..off + w].try_into().unwrap());
        let level = i32::from_ne_bytes(bytes[off + w..off + w + 4].try_into().unwrap());
        let kind = i32::from_ne_bytes(bytes[off + w + 4..off + w + 8].try_into().unwrap());
        if len < hdr || off + len > end { break; }
        if level == SOL_SOCKET && kind == SCM_RIGHTS {
            let mut k = off + hdr;
            while k + 4 <= off + len {
                let d = i32::from_ne_bytes(bytes[k..k + 4].try_into().unwrap());
                // one per packet is the protocol's; any more would be leaked: closed
                if got.is_none() { got = Some(d) } else { unsafe { libc_close(d) }; }
                k += 4;
            }
        }
        off += (len + w - 1) / w * w;
    }
    Ok((n, got))
}

extern "C" {
    #[link_name = "close"]
    fn libc_close(fd: c_int) -> c_int;
}

#[cfg(target_arch = "arm")]
mod nr { pub const GETPID: i32 = 20; pub const GETTID: i32 = 224; pub const TGKILL: i32 = 268; }
#[cfg(target_arch = "x86_64")]
mod nr { pub const GETPID: i32 = 39; pub const GETTID: i32 = 186; pub const TGKILL: i32 = 234; }

/// The development flag (the plugin's card): a system call the sandbox refuses raises SIGSYS instead of answering
/// EPERM.  The handler writes which call it was into the log (a write, all a handler can do), then the signal again
/// with its default: the plugin ends, and its card says the sandbox stopped it.  As the C client does.
extern "C" fn on_sigsys(sig: c_int, info: *const u8, _uc: *const c_void) {
    // siginfo_t: three ints, then the union aligned to a pointer: _sigsys { void *call_addr; int syscall; ... }
    let p = std::mem::size_of::<usize>();
    let off = (12 + p - 1) / p * p + p;
    let mut nr = unsafe { std::ptr::read_unaligned(info.add(off) as *const i32) };
    let mut b = [0u8; 120];
    let head = b"the sandbox refused system call ";
    b[..head.len()].copy_from_slice(head);
    let mut n = head.len();
    if nr < 0 { nr = 0; }
    let mut d = 1;
    while nr / d >= 10 { d *= 10; }
    while d > 0 { b[n] = b'0' + (nr / d % 10) as u8; n += 1; d /= 10; }
    let tail = b" (sdk/filter.def has what may be called)\n";
    b[n..n + tail.len()].copy_from_slice(tail);
    n += tail.len();
    unsafe {
        write(2, b.as_ptr() as *const c_void, n);
        let dfl = sigaction_t { handler: 0, mask: [0; 32], flags: 0, restorer: 0 };
        sigaction(sig, &dfl, std::ptr::null_mut());
        let pid = syscall(nr::GETPID as c_long);
        let tid = syscall(nr::GETTID as c_long);
        syscall(nr::TGKILL as c_long, pid, tid, sig as c_long);
    }
}

pub fn sigsys_handler() {
    let sa = sigaction_t { handler: on_sigsys as extern "C" fn(c_int, *const u8, *const c_void) as usize, mask: [0; 32], flags: SA_SIGINFO, restorer: 0 };
    unsafe { sigaction(SIGSYS, &sa, std::ptr::null_mut()) };
}
