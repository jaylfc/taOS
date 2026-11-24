// SPDX-License-Identifier: AGPL-3.0-or-later
//! The LAN face of the engine: serves the controller page and the WebSocket.
//!
//! Every connection is judged by its source address first. The page and its
//! assets are public to the LAN (they hold no power); pairing needs the code;
//! the WebSocket needs a device token in its handshake, and a handshake
//! without a valid token never becomes a WebSocket at all.

use crate::engine::{tokens_css, Engine};
use crate::proto::{ClientMsg, ServerMsg, Session};
use std::io::{Read, Write};
use std::net::{IpAddr, Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tungstenite::handshake::server::{ErrorResponse, Request, Response};
use tungstenite::http::{header::HeaderValue, StatusCode};
use tungstenite::protocol::WebSocketConfig;
use tungstenite::Message;

const INDEX_HTML: &str = include_str!("../web/index.html");
const APP_JS: &str = include_str!("../web/app.js");
const APP_CSS: &str = include_str!("../web/app.css");
const MANIFEST: &str = include_str!("../web/manifest.webmanifest");
const ICON_SVG: &str = include_str!("../web/icon.svg");
const ICON_PNG: &[u8] = include_bytes!("../web/icon-180.png");

pub const SUBPROTOCOL: &str = "taos-input.v1";
const MAX_HEAD: usize = 8192;
const MAX_CONNS: usize = 32;
const IDLE_LIMIT: Duration = Duration::from_secs(15);
const MAX_MSGS_PER_SEC: u32 = 4000;

static LIVE: AtomicUsize = AtomicUsize::new(0);
static PAIR_GATE: Mutex<Vec<Instant>> = Mutex::new(Vec::new());

pub fn serve(engine: Arc<Engine>, listener: TcpListener) {
    for conn in listener.incoming() {
        if engine.stopping() {
            break;
        }
        let Ok(stream) = conn else { continue };
        if LIVE.fetch_add(1, Ordering::SeqCst) >= MAX_CONNS {
            LIVE.fetch_sub(1, Ordering::SeqCst);
            let _ = stream.shutdown(Shutdown::Both);
            continue;
        }
        let e = engine.clone();
        std::thread::spawn(move || {
            handle(&e, stream);
            LIVE.fetch_sub(1, Ordering::SeqCst);
        });
    }
}

struct Head {
    len: usize,
    method: String,
    path: String,
    headers: Vec<(String, String)>,
}

impl Head {
    fn get(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.as_str())
    }
}

fn parse_head(raw: &[u8]) -> Option<Head> {
    let end = raw.windows(4).position(|w| w == b"\r\n\r\n")? + 4;
    let text = std::str::from_utf8(&raw[..end]).ok()?;
    let mut lines = text.split("\r\n");
    let mut first = lines.next()?.split(' ');
    let method = first.next()?.to_string();
    let path = first.next()?.to_string();
    let headers = lines
        .filter(|l| !l.is_empty())
        .filter_map(|l| {
            l.split_once(':')
                .map(|(k, v)| (k.trim().to_ascii_lowercase(), v.trim().to_string()))
        })
        .collect();
    Some(Head {
        len: end,
        method,
        path,
        headers,
    })
}

fn respond(s: &mut TcpStream, status: &str, ctype: &str, body: &[u8], cache: &str) {
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nCache-Control: {cache}\r\n\
         X-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nX-Frame-Options: DENY\r\n\
         Content-Security-Policy: default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'none'\r\n\
         Connection: close\r\n\r\n",
        body.len()
    );
    let _ = s.write_all(head.as_bytes());
    let _ = s.write_all(body);
    let _ = s.flush();
}

fn handle(engine: &Arc<Engine>, mut s: TcpStream) {
    let Ok(peer) = s.peer_addr() else { return };
    if !engine.policy.allows_peer(peer.ip()) {
        let _ = s.shutdown(Shutdown::Both);
        return;
    }
    let _ = s.set_nodelay(true);
    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
    let _ = s.set_write_timeout(Some(Duration::from_secs(5)));
    // Look at the request head without consuming it, so a WebSocket upgrade
    // can be handed to the WebSocket library untouched.
    let mut buf = vec![0u8; MAX_HEAD];
    let deadline = Instant::now() + Duration::from_secs(5);
    let head = loop {
        let n = match s.peek(&mut buf) {
            Ok(0) => return,
            Ok(n) => n,
            Err(_) => return,
        };
        if let Some(h) = parse_head(&buf[..n]) {
            break h;
        }
        if n >= MAX_HEAD || Instant::now() > deadline {
            respond(
                &mut s,
                "431 Request Header Fields Too Large",
                "text/plain",
                b"too large",
                "no-store",
            );
            return;
        }
        std::thread::sleep(Duration::from_millis(4));
    };
    let is_ws = head
        .get("upgrade")
        .map(|u| u.eq_ignore_ascii_case("websocket"))
        .unwrap_or(false);
    if is_ws {
        ws_session(engine, s, peer.ip());
    } else {
        let mut consumed = vec![0u8; head.len];
        if s.read_exact(&mut consumed).is_err() {
            return;
        }
        http(engine, &mut s, head);
    }
}

fn http(engine: &Arc<Engine>, s: &mut TcpStream, head: Head) {
    let path = head.path.split('?').next().unwrap_or("/");
    match (head.method.as_str(), path) {
        ("GET", "/") | ("GET", "/index.html") => respond(
            s,
            "200 OK",
            "text/html; charset=utf-8",
            INDEX_HTML.as_bytes(),
            "no-cache",
        ),
        ("GET", "/app.js") => respond(
            s,
            "200 OK",
            "text/javascript; charset=utf-8",
            APP_JS.as_bytes(),
            "no-cache",
        ),
        ("GET", "/app.css") => respond(
            s,
            "200 OK",
            "text/css; charset=utf-8",
            APP_CSS.as_bytes(),
            "no-cache",
        ),
        ("GET", "/tokens.css") => respond(
            s,
            "200 OK",
            "text/css; charset=utf-8",
            tokens_css(&engine.theme).as_bytes(),
            "no-cache",
        ),
        ("GET", "/manifest.webmanifest") => respond(
            s,
            "200 OK",
            "application/manifest+json",
            MANIFEST.as_bytes(),
            "no-cache",
        ),
        ("GET", "/icon.svg") => respond(
            s,
            "200 OK",
            "image/svg+xml",
            ICON_SVG.as_bytes(),
            "no-cache",
        ),
        ("GET", "/icon-180.png") => respond(s, "200 OK", "image/png", ICON_PNG, "no-cache"),
        ("POST", "/api/pair") => pair(engine, s, &head),
        _ => respond(s, "404 Not Found", "text/plain", b"not found", "no-store"),
    }
}

fn json(s: &mut TcpStream, status: &str, v: serde_json::Value) {
    respond(
        s,
        status,
        "application/json",
        v.to_string().as_bytes(),
        "no-store",
    );
}

/// At most 20 pairing attempts a minute from anyone, on top of the code's own
/// five-guess limit.
fn pair_gate_open() -> bool {
    let mut g = PAIR_GATE.lock().unwrap();
    let now = Instant::now();
    g.retain(|t| now.duration_since(*t) < Duration::from_secs(60));
    if g.len() >= 20 {
        return false;
    }
    g.push(now);
    true
}

fn pair(engine: &Arc<Engine>, s: &mut TcpStream, head: &Head) {
    let len: usize = head
        .get("content-length")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    if len == 0 || len > 1024 {
        return json(
            s,
            "400 Bad Request",
            serde_json::json!({"ok": false, "error": "bad request"}),
        );
    }
    let mut body = vec![0u8; len];
    if s.read_exact(&mut body).is_err() {
        return;
    }
    if !pair_gate_open() {
        return json(
            s,
            "429 Too Many Requests",
            serde_json::json!({"ok": false, "error": "slow down"}),
        );
    }
    #[derive(serde::Deserialize)]
    struct Req {
        code: String,
        #[serde(default)]
        name: String,
    }
    let Ok(req) = serde_json::from_slice::<Req>(&body) else {
        return json(
            s,
            "400 Bad Request",
            serde_json::json!({"ok": false, "error": "bad request"}),
        );
    };
    match engine.auth.pair(&req.code, &req.name) {
        Ok((id, token)) => json(
            s,
            "200 OK",
            serde_json::json!({"ok": true, "id": id, "token": token}),
        ),
        Err(e) => {
            std::thread::sleep(Duration::from_millis(300));
            json(
                s,
                "403 Forbidden",
                serde_json::json!({"ok": false, "error": e.to_string()}),
            )
        }
    }
}

fn deny(status: StatusCode, why: &str) -> ErrorResponse {
    let mut r = ErrorResponse::new(Some(why.to_string()));
    *r.status_mut() = status;
    r
}

/// The host part of an Origin header equals the Host header: the page came
/// from this very engine, not from another site in the phone's browser.
pub fn origin_matches(origin: &str, host: &str) -> bool {
    let rest = origin.split_once("://").map(|(_, r)| r).unwrap_or(origin);
    let rest = rest.split('/').next().unwrap_or("");
    rest.eq_ignore_ascii_case(host)
}

fn ws_session(engine: &Arc<Engine>, stream: TcpStream, _peer: IpAddr) {
    let mut device = None;
    #[allow(clippy::result_large_err)] // the error type is the library's own callback signature
    let verify = |req: &Request, mut resp: Response| -> Result<Response, ErrorResponse> {
        let h = req.headers();
        let host = h.get("host").and_then(|v| v.to_str().ok()).unwrap_or("");
        if host.is_empty() {
            return Err(deny(StatusCode::BAD_REQUEST, "no host"));
        }
        if let Some(o) = h.get("origin").and_then(|v| v.to_str().ok()) {
            if !origin_matches(o, host) {
                return Err(deny(StatusCode::FORBIDDEN, "wrong origin"));
            }
        }
        let offered = h
            .get("sec-websocket-protocol")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        let mut parts = offered.split(',').map(|p| p.trim());
        if parts.next() != Some(SUBPROTOCOL) {
            return Err(deny(StatusCode::UNAUTHORIZED, "unauthorized"));
        }
        let token = parts.next().unwrap_or("");
        match engine.auth.verify(token) {
            Some(d) => device = Some(d),
            None => return Err(deny(StatusCode::UNAUTHORIZED, "unauthorized")),
        }
        resp.headers_mut().insert(
            "Sec-WebSocket-Protocol",
            HeaderValue::from_static(SUBPROTOCOL),
        );
        Ok(resp)
    };
    let cfg = WebSocketConfig::default()
        .max_message_size(Some(16 * 1024))
        .max_frame_size(Some(16 * 1024));
    let Ok(mut ws) = tungstenite::accept_hdr_with_config(stream, verify, Some(cfg)) else {
        return;
    };
    let Some(dev) = device else { return };
    let _ = ws
        .get_mut()
        .set_read_timeout(Some(Duration::from_millis(20)));

    let kick = engine.claim(&dev.id);
    engine.auth.touch(&dev.id);
    let mut session = Session::new();
    let mut up = engine.injector.compositor_up();
    let send = |ws: &mut tungstenite::WebSocket<TcpStream>, m: &ServerMsg| -> bool {
        match serde_json::to_string(m) {
            Ok(t) => ws.send(Message::text(t)).is_ok(),
            Err(_) => false,
        }
    };
    let apply = |fx: crate::proto::Effects, ws: &mut tungstenite::WebSocket<TcpStream>| -> bool {
        for c in fx.cmds {
            engine.injector.send(c);
        }
        if let Some(d) = fx.workspace {
            let e = engine.clone();
            std::thread::spawn(move || e.workspace(d));
        }
        fx.replies.iter().all(|m| send(ws, m))
    };

    let mut ok = send(
        &mut ws,
        &ServerMsg::Hello {
            proto: 1,
            name: dev.name.clone(),
            compositor: up,
        },
    );
    let mut last_rx = Instant::now();
    let mut window = (Instant::now(), 0u32);
    let mut last_check = Instant::now();
    while ok {
        if kick.load(Ordering::SeqCst) || engine.stopping() {
            break;
        }
        if last_check.elapsed() > Duration::from_secs(1) {
            last_check = Instant::now();
            if !engine.auth.exists(&dev.id) {
                break; // revoked while connected
            }
        }
        if last_rx.elapsed() > IDLE_LIMIT {
            break;
        }
        match ws.read() {
            Ok(Message::Text(t)) => {
                last_rx = Instant::now();
                if window.0.elapsed() > Duration::from_secs(1) {
                    window = (Instant::now(), 0);
                }
                window.1 += 1;
                if window.1 > MAX_MSGS_PER_SEC {
                    break;
                }
                if let Ok(m) = serde_json::from_str::<ClientMsg>(&t) {
                    let fx = session.on_message(m, engine.now_ms());
                    ok = apply(fx, &mut ws);
                }
            }
            Ok(Message::Close(_)) => break,
            Ok(Message::Binary(_)) => break,
            Ok(_) => last_rx = Instant::now(),
            Err(tungstenite::Error::Io(e))
                if matches!(
                    e.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) => {}
            Err(_) => break,
        }
        let fx = session.tick(engine.now_ms());
        if ok {
            ok = apply(fx, &mut ws);
        }
        let now_up = engine.injector.compositor_up();
        if now_up != up {
            up = now_up;
            ok = ok && send(&mut ws, &ServerMsg::State { compositor: up });
        }
    }
    // Whatever way this ended, nothing the controller held stays held.
    for c in session.finish().cmds {
        engine.injector.send(c);
    }
    engine.release(&kick);
    let _ = ws.close(None);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn origin_must_be_the_engine_itself() {
        assert!(origin_matches(
            "http://192.168.1.5:7981",
            "192.168.1.5:7981"
        ));
        assert!(origin_matches("http://Host.local:7981", "host.local:7981"));
        assert!(!origin_matches("http://evil.example", "192.168.1.5:7981"));
        assert!(!origin_matches(
            "http://192.168.1.5:7981.evil.example",
            "192.168.1.5:7981"
        ));
        assert!(!origin_matches("http://192.168.1.5:80", "192.168.1.5:7981"));
    }

    #[test]
    fn head_parser_reads_method_path_and_lowercases_names() {
        let h = parse_head(b"POST /api/pair HTTP/1.1\r\nHost: a\r\nContent-Length: 5\r\n\r\nhello")
            .unwrap();
        assert_eq!(
            (h.method.as_str(), h.path.as_str(), h.len),
            ("POST", "/api/pair", 55)
        );
        assert_eq!(h.get("content-length"), Some("5"));
        assert!(parse_head(b"GET / HTTP/1.1\r\nHost: a\r\n").is_none());
    }
}
