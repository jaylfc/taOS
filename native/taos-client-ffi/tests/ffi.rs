//! Drives the C ABI exactly as the Qt wrapper does, against the Python fake
//! controller (native/testing/fake_controller.py).
#![allow(unsafe_code)]

use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use taos_client_ffi::*;

struct Fake {
    child: Child,
    port: u16,
}

impl Fake {
    fn start() -> Option<Fake> {
        let script = concat!(env!("CARGO_MANIFEST_DIR"), "/../testing/fake_controller.py");
        let mut child = Command::new("python3")
            .arg(script)
            .stdout(Stdio::piped())
            .spawn()
            .ok()?;
        let mut line = String::new();
        BufReader::new(child.stdout.as_mut()?)
            .read_line(&mut line)
            .ok()?;
        let port = line.trim().strip_prefix("LISTENING ")?.parse().ok()?;
        Some(Fake { child, port })
    }
    fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }
    fn http(&self, method: &str, path: &str, body: &str) -> String {
        let mut s = TcpStream::connect(("127.0.0.1", self.port)).unwrap();
        write!(
            s,
            "{method} {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).unwrap();
        out.split("\r\n\r\n").nth(1).unwrap_or("").to_string()
    }
    fn ctl(&self, body: &str) {
        self.http("POST", "/__fake/config", body);
    }
    fn stat(&self, key: &str) -> i64 {
        let v: serde_json::Value =
            serde_json::from_str(&self.http("GET", "/__fake/stats", "")).unwrap();
        v[key].as_i64().unwrap()
    }
}

impl Drop for Fake {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default)]
struct Sink {
    events: Mutex<Vec<(u64, c_int, String)>>,
    cv: Condvar,
}

unsafe extern "C" fn cb(user: *mut c_void, id: u64, kind: c_int, json: *const c_char, _len: usize) {
    let sink = unsafe { &*(user as *const Sink) };
    let s = unsafe { CStr::from_ptr(json) }
        .to_string_lossy()
        .into_owned();
    sink.events.lock().unwrap().push((id, kind, s));
    sink.cv.notify_all();
}

impl Sink {
    fn wait_for(
        &self,
        what: impl Fn(&[(u64, c_int, String)]) -> bool,
    ) -> Vec<(u64, c_int, String)> {
        let deadline = Instant::now() + Duration::from_secs(10);
        let mut g = self.events.lock().unwrap();
        while !what(&g) {
            let left = deadline.saturating_duration_since(Instant::now());
            assert!(!left.is_zero(), "timed out; events so far: {:?}", *g);
            g = self.cv.wait_timeout(g, left).unwrap().0;
        }
        g.clone()
    }
}

fn new_client(url: &str, sink: &Arc<Sink>, bearer: Option<&str>) -> *mut TaosClient {
    let u = CString::new(url).unwrap();
    let b = bearer.map(|b| CString::new(b).unwrap());
    let cfg = TaosConfig {
        base_url: u.as_ptr(),
        session_json: std::ptr::null(),
        bearer_token: b.as_ref().map_or(std::ptr::null(), |b| b.as_ptr()),
        request_timeout_ms: 5000,
        connect_timeout_ms: 2000,
        sse_backoff_base_ms: 20,
        sse_backoff_cap_ms: 100,
        sse_idle_timeout_ms: 0,
    };
    let mut err: *mut c_char = std::ptr::null_mut();
    let c = unsafe { taos_client_new(&cfg, Some(cb), Arc::as_ptr(sink) as *mut c_void, &mut err) };
    assert!(!c.is_null());
    c
}

fn call(c: *mut TaosClient, id: u64, op: &str, args: &str) -> c_int {
    let (o, a) = (CString::new(op).unwrap(), CString::new(args).unwrap());
    unsafe { taos_call(c, id, o.as_ptr(), a.as_ptr()) }
}

fn open_stream(c: *mut TaosClient, id: u64) -> c_int {
    let p = CString::new("/api/os/events").unwrap();
    unsafe { taos_stream_open(c, id, p.as_ptr(), std::ptr::null()) }
}

fn terminal(sink: &Sink, id: u64) -> (c_int, serde_json::Value) {
    let ev = sink.wait_for(|e| {
        e.iter()
            .any(|x| x.0 == id && (x.1 == TAOS_EV_RESULT || x.1 == TAOS_EV_FAILED))
    });
    let e = ev
        .iter()
        .find(|x| x.0 == id && (x.1 == TAOS_EV_RESULT || x.1 == TAOS_EV_FAILED))
        .unwrap();
    (e.1, serde_json::from_str(&e.2).unwrap())
}

fn sign_in(c: *mut TaosClient, sink: &Sink, id: u64) {
    call(
        c,
        id,
        "auth.login",
        r#"{"username":"alex","password":"hunter2-secret-pw"}"#,
    );
    assert_eq!(terminal(sink, id).0, TAOS_EV_RESULT);
}

#[test]
fn login_list_archive_and_error_mapping() {
    let Some(fake) = Fake::start() else {
        return eprintln!("no python3; skipped");
    };
    let sink = Arc::new(Sink::default());
    let c = new_client(&fake.url(), &sink, None);

    // Unauthenticated before sign-in.
    call(c, 1, "notifications.list", "{}");
    let (k, v) = terminal(&sink, 1);
    assert_eq!(
        (k, v["kind"].as_str()),
        (TAOS_EV_FAILED, Some("unauthenticated"))
    );
    assert_eq!(v["status"], 401);

    // Wrong password: the controller's own words come through.
    call(
        c,
        2,
        "auth.login",
        r#"{"username":"alex","password":"nope"}"#,
    );
    let (_, v) = terminal(&sink, 2);
    assert_eq!(v["kind"], "unauthenticated");
    assert_eq!(v["message"], "Invalid username or password");

    sign_in(c, &sink, 3);
    call(c, 4, "notifications.list", "{}");
    let (k, v) = terminal(&sink, 4);
    assert_eq!(k, TAOS_EV_RESULT);
    assert_eq!(v.as_array().unwrap().len(), 3);

    // A POST passes the CSRF check (the client copies the cookie into the header).
    call(c, 5, "notifications.mark_read", r#"{"id":12}"#);
    assert_eq!(terminal(&sink, 5).0, TAOS_EV_RESULT);
    call(c, 6, "notifications.archive", r#"{"id":12}"#);
    assert_eq!(terminal(&sink, 6).1["outcome"], "kept_pending_request");

    // 404 and 409 with messages.
    call(c, 7, "decisions.get", r#"{"id":"nope"}"#);
    assert_eq!(terminal(&sink, 7).1["kind"], "not_found");
    call(c, 8, "decisions.answer", r#"{"id":"dec_1","value":"pg"}"#);
    assert_eq!(terminal(&sink, 8).0, TAOS_EV_RESULT);
    call(c, 9, "decisions.answer", r#"{"id":"dec_1","value":"pg"}"#);
    let (_, v) = terminal(&sink, 9);
    assert_eq!(v["kind"], "conflict");
    assert!(v["message"].as_str().unwrap().contains("already answered"));

    // 5xx, injected.
    fake.ctl(
        r#"{"fail":{"path":"/api/activity","status":500,"message":"disk on fire","times":1}}"#,
    );
    call(c, 10, "dashboard.activity", "{}");
    let (_, v) = terminal(&sink, 10);
    assert_eq!(
        (v["kind"].as_str(), v["message"].as_str()),
        (Some("server"), Some("disk on fire"))
    );

    call(c, 11, "dashboard.snapshot", "{}");
    let (k, v) = terminal(&sink, 11);
    assert_eq!(k, TAOS_EV_RESULT, "{v}");
    assert!(v["activity"].is_object());

    // Raw escape hatch and unknown op.
    call(c, 12, "http", r#"{"method":"GET","path":"/api/models"}"#);
    assert_eq!(terminal(&sink, 12).0, TAOS_EV_RESULT);
    call(c, 13, "bogus.op", "{}");
    assert_eq!(terminal(&sink, 13).1["kind"], "invalid_input");

    // Duplicate id is refused synchronously.
    fake.ctl(r#"{"delay_ms":300}"#);
    assert_eq!(call(c, 14, "auth.me", "{}"), TAOS_OK);
    assert_eq!(call(c, 14, "auth.me", "{}"), TAOS_ERR_ID_IN_USE);

    assert_eq!(unsafe { taos_client_free(c) }, TAOS_OK);
}

#[test]
fn network_down_is_a_network_error() {
    let sink = Arc::new(Sink::default());
    let c = new_client("http://127.0.0.1:9", &sink, None);
    call(c, 1, "auth.me", "{}");
    let (k, v) = terminal(&sink, 1);
    assert_eq!((k, v["kind"].as_str()), (TAOS_EV_FAILED, Some("network")));
    unsafe { taos_client_free(c) };
}

#[test]
fn bearer_token_and_session_export() {
    let fake = Fake::start().expect("python3 fake controller must start");
    let sink = Arc::new(Sink::default());
    let c = new_client(&fake.url(), &sink, Some("local-token-123"));
    call(c, 1, "notifications.list", "{}");
    assert_eq!(terminal(&sink, 1).0, TAOS_EV_RESULT);
    assert!(unsafe { taos_session_export(c) }.is_null());

    // Replace the token with a bad one: 401. Remove it: 401.
    let bad = CString::new("wrong").unwrap();
    unsafe { taos_set_bearer(c, bad.as_ptr()) };
    call(c, 2, "notifications.list", "{}");
    assert_eq!(terminal(&sink, 2).1["kind"], "unauthenticated");
    unsafe { taos_set_bearer(c, std::ptr::null()) };
    assert_eq!(unsafe { taos_has_credentials(c) }, 0);

    // Sign in, export, restore into a second client.
    sign_in(c, &sink, 3);
    let j = unsafe { taos_session_export(c) };
    assert!(!j.is_null());
    let session = unsafe { CStr::from_ptr(j) }.to_str().unwrap().to_string();
    unsafe { taos_string_free(j) };
    let u = CString::new(fake.url()).unwrap();
    let s = CString::new(session).unwrap();
    let cfg = TaosConfig {
        base_url: u.as_ptr(),
        session_json: s.as_ptr(),
        bearer_token: std::ptr::null(),
        request_timeout_ms: 5000,
        connect_timeout_ms: 2000,
        sse_backoff_base_ms: 0,
        sse_backoff_cap_ms: 0,
        sse_idle_timeout_ms: 0,
    };
    let sink2 = Arc::new(Sink::default());
    let mut err = std::ptr::null_mut();
    let c2 =
        unsafe { taos_client_new(&cfg, Some(cb), Arc::as_ptr(&sink2) as *mut c_void, &mut err) };
    call(c2, 1, "auth.me", "{}");
    assert_eq!(terminal(&sink2, 1).0, TAOS_EV_RESULT);
    unsafe { taos_client_free(c) };
    unsafe { taos_client_free(c2) };
}

#[test]
fn stream_events_reconnect_cancel_and_free() {
    let fake = Fake::start().expect("python3 fake controller must start");
    fake.ctl(r#"{"heartbeat_ms":50}"#);
    let sink = Arc::new(Sink::default());
    let c = new_client(&fake.url(), &sink, None);

    // Not signed in: the stream ends with unauthenticated, once.
    assert_eq!(open_stream(c, 100), TAOS_OK);
    let ev = sink.wait_for(|e| e.iter().any(|x| x.0 == 100 && x.1 == TAOS_EV_STREAM_ENDED));
    assert!(ev
        .iter()
        .any(|x| x.0 == 100 && x.2.contains("unauthenticated")));

    sign_in(c, &sink, 1);
    assert_eq!(open_stream(c, 101), TAOS_OK);
    sink.wait_for(|e| {
        e.iter()
            .any(|x| x.0 == 101 && x.1 == TAOS_EV_STREAM_CONNECTED)
    });
    sink.wait_for(|e| e.iter().any(|x| x.0 == 101 && x.1 == TAOS_EV_STREAM_EVENT));
    assert_eq!(fake.stat("sse_open"), 1);

    // Server drops the connection: retry notice, then events again.
    let before = sink.events.lock().unwrap().len();
    fake.ctl(r#"{"drop_sse":true}"#);
    sink.wait_for(|e| {
        e.iter()
            .any(|x| x.0 == 101 && x.1 == TAOS_EV_STREAM_RETRYING)
    });
    sink.wait_for(|e| {
        e.iter()
            .skip(before)
            .any(|x| x.0 == 101 && x.1 == TAOS_EV_STREAM_EVENT)
    });
    assert_eq!(fake.stat("sse_total"), 2);

    // A pushed event arrives with its payload.
    fake.ctl(r#"{"push":{"kind":"notification.added","id":"n1"}}"#);
    sink.wait_for(|e| {
        e.iter()
            .any(|x| x.0 == 101 && x.2.contains("notification.added"))
    });

    // Cancel closes the connection on the server and silences the id.
    assert_eq!(unsafe { taos_cancel(c, 101) }, TAOS_OK);
    let n = sink.events.lock().unwrap().len();
    let t = Instant::now();
    while fake.stat("sse_open") != 0 {
        assert!(
            t.elapsed() < Duration::from_secs(5),
            "connection leaked after cancel"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    std::thread::sleep(Duration::from_millis(200));
    assert!(sink.events.lock().unwrap()[n..].iter().all(|x| x.0 != 101));

    // Free with a stream open: connection closes, no callback afterwards.
    assert_eq!(open_stream(c, 102), TAOS_OK);
    sink.wait_for(|e| e.iter().any(|x| x.0 == 102 && x.1 == TAOS_EV_STREAM_EVENT));
    assert_eq!(unsafe { taos_client_free(c) }, TAOS_OK);
    let n = sink.events.lock().unwrap().len();
    let t = Instant::now();
    while fake.stat("sse_open") != 0 {
        assert!(
            t.elapsed() < Duration::from_secs(5),
            "connection leaked after free"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    std::thread::sleep(Duration::from_millis(200));
    assert_eq!(
        sink.events.lock().unwrap().len(),
        n,
        "callback ran after free"
    );
}

#[test]
fn free_from_inside_a_callback_is_refused() {
    let fake = Fake::start().expect("python3 fake controller must start");
    struct Ctx {
        client: Mutex<usize>,
        rc: Mutex<Option<c_int>>,
        cv: Condvar,
    }
    unsafe extern "C" fn cb2(user: *mut c_void, _id: u64, _k: c_int, _j: *const c_char, _l: usize) {
        let ctx = unsafe { &*(user as *const Ctx) };
        let c = *ctx.client.lock().unwrap() as *mut TaosClient;
        *ctx.rc.lock().unwrap() = Some(unsafe { taos_client_free(c) });
        ctx.cv.notify_all();
    }
    let ctx = Arc::new(Ctx {
        client: Mutex::new(0),
        rc: Mutex::new(None),
        cv: Condvar::new(),
    });
    let u = CString::new(fake.url()).unwrap();
    let cfg = TaosConfig {
        base_url: u.as_ptr(),
        session_json: std::ptr::null(),
        bearer_token: std::ptr::null(),
        request_timeout_ms: 3000,
        connect_timeout_ms: 1000,
        sse_backoff_base_ms: 0,
        sse_backoff_cap_ms: 0,
        sse_idle_timeout_ms: 0,
    };
    let mut err = std::ptr::null_mut();
    let c = unsafe { taos_client_new(&cfg, Some(cb2), Arc::as_ptr(&ctx) as *mut c_void, &mut err) };
    *ctx.client.lock().unwrap() = c as usize;
    call(c, 1, "auth.me", "{}");
    let mut g = ctx.rc.lock().unwrap();
    let t = Instant::now();
    while g.is_none() {
        g = ctx
            .cv
            .wait_timeout(g, Duration::from_millis(100))
            .unwrap()
            .0;
        assert!(t.elapsed() < Duration::from_secs(5));
    }
    assert_eq!(*g, Some(TAOS_ERR_IN_CALLBACK));
    drop(g);
    // Still usable afterwards.
    assert_eq!(call(c, 2, "auth.me", "{}"), TAOS_OK);
    assert_eq!(unsafe { taos_client_free(c) }, TAOS_OK);
}
