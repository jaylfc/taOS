// SPDX-License-Identifier: AGPL-3.0-or-later
//! A reference client for the engine: pairs and opens an encrypted session
//! the same way the phone page does. The end to end tests use it, and it is
//! the executable description of the protocol (see README.md).

use crate::auth::{hex, open_once, AAD_C2S, AAD_S2C};
use crate::secure::{self, Channel, SUBPROTOCOL, T_AUTH, T_HELLO, T_SERVER};
use crate::server::unhex;
use p256::SecretKey;
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;
use tungstenite::client::IntoClientRequest;
use tungstenite::{Message, WebSocket};

/// What a paired phone keeps.
pub struct Creds {
    pub id: [u8; 6],
    pub secret: SecretKey,
    /// The engine's public key, learned during pairing and pinned.
    pub engine_pub: [u8; 65],
}

pub fn post(
    stream: &mut TcpStream,
    host: &str,
    path: &str,
    body: &Value,
) -> Result<(u16, Value), String> {
    let b = body.to_string();
    write!(
        stream,
        "POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{b}",
        b.len()
    )
    .map_err(|e| e.to_string())?;
    let mut raw = String::new();
    stream.set_read_timeout(Some(Duration::from_secs(10))).ok();
    stream.read_to_string(&mut raw).map_err(|e| e.to_string())?;
    let status = raw
        .split(' ')
        .nth(1)
        .and_then(|c| c.parse().ok())
        .ok_or_else(|| format!("no status in {raw:?}"))?;
    let v = serde_json::from_str(raw.split("\r\n\r\n").nth(1).unwrap_or("")).unwrap_or(Value::Null);
    Ok((status, v))
}

fn h32(v: &Value, k: &str) -> Result<[u8; 32], String> {
    unhex(v[k].as_str().unwrap_or(""))
        .and_then(|b| b.try_into().ok())
        .ok_or_else(|| format!("bad {k}"))
}

/// Pair as a phone that types `code`. `connect` opens a fresh connection per
/// request (the engine answers one request per connection).
pub fn pair(
    mut connect: impl FnMut() -> std::io::Result<TcpStream>,
    host: &str,
    code: &str,
    name: &str,
) -> Result<Creds, String> {
    let mut call = |path: &str, body: Value| -> Result<Value, String> {
        let mut s = connect().map_err(|e| e.to_string())?;
        let (status, v) = post(&mut s, host, path, &body)?;
        if status != 200 {
            return Err(v["error"].as_str().unwrap_or("refused").to_string());
        }
        Ok(v)
    };
    let begin = call("/api/pair/begin", json!({}))?;
    let pid = begin["pid"].as_str().ok_or("no pid")?.to_string();
    let sid: [u8; 16] = unhex(begin["sid"].as_str().unwrap_or(""))
        .and_then(|b| b.try_into().ok())
        .ok_or("bad sid")?;
    let hello = crate::pake::client_start(code, &sid).map_err(|_| "pake")?;
    let init = call("/api/pair/init", json!({"pid": pid, "ya": hex(&hello.ya)}))?;
    let keys = hello
        .finish(&h32(&init, "yb")?)
        .map_err(|_| "bad engine point")?;
    if !crate::auth::ct_eq(&keys.confirm_s, &h32(&init, "confirm")?) {
        return Err("the engine did not prove it knows the code".into());
    }
    let secret = secure::new_secret();
    let mut plain = secure::public_bytes(&secret).to_vec();
    plain.extend_from_slice(name.as_bytes());
    let sealed = seal(&keys.enc_c2s, &plain)?;
    let fin = call(
        "/api/pair/finish",
        json!({"pid": pid, "confirm": hex(&keys.confirm_c), "blob": hex(&sealed)}),
    )?;
    let id: [u8; 6] = unhex(fin["id"].as_str().unwrap_or(""))
        .and_then(|b| b.try_into().ok())
        .ok_or("bad id")?;
    let blob = unhex(fin["blob"].as_str().unwrap_or("")).ok_or("bad blob")?;
    let engine_pub: [u8; 65] = open_once(&keys.enc_s2c, AAD_S2C, &blob)
        .map_err(|e| e.to_string())?
        .try_into()
        .map_err(|_| "bad key")?;
    Ok(Creds {
        id,
        secret,
        engine_pub,
    })
}

fn seal(key: &[u8; 32], plain: &[u8]) -> Result<Vec<u8>, String> {
    use aes_gcm::aead::{Aead, KeyInit, Payload};
    let c = aes_gcm::Aes256Gcm::new_from_slice(key).map_err(|e| e.to_string())?;
    c.encrypt(
        aes_gcm::Nonce::from_slice(&[0u8; 12]),
        Payload {
            msg: plain,
            aad: AAD_C2S,
        },
    )
    .map_err(|e| e.to_string())
}

/// An open, authenticated session. `ws` and `chan` are public so tests can
/// send frames the page never would (replays, tampering, reordering).
pub struct Conn {
    pub ws: WebSocket<TcpStream>,
    pub chan: Channel,
}

#[derive(Debug)]
pub enum ConnectError {
    /// The engine refused or closed during the handshake.
    Refused(String),
}

pub fn connect(
    stream: TcpStream,
    host: &str,
    origin: Option<&str>,
    creds: &Creds,
) -> Result<Conn, ConnectError> {
    let refuse = |e: &dyn std::fmt::Display| ConnectError::Refused(e.to_string());
    let mut req = format!("ws://{host}/ws")
        .into_client_request()
        .map_err(|e| refuse(&e))?;
    req.headers_mut()
        .insert("Sec-WebSocket-Protocol", SUBPROTOCOL.parse().unwrap());
    if let Some(o) = origin {
        req.headers_mut().insert("Origin", o.parse().unwrap());
    }
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok();
    let (mut ws, _) = tungstenite::client(req, stream).map_err(|e| refuse(&e))?;
    let eph = secure::new_secret();
    let ec = secure::public_bytes(&eph);
    let mut nc = [0u8; 16];
    crate::auth::random_bytes(&mut nc).map_err(|e| refuse(&e))?;
    let mut hello = vec![T_HELLO];
    hello.extend_from_slice(&creds.id);
    hello.extend_from_slice(&nc);
    hello.extend_from_slice(&ec);
    ws.send(Message::binary(hello)).map_err(|e| refuse(&e))?;
    let reply = match ws.read() {
        Ok(Message::Binary(b)) => b.to_vec(),
        other => return Err(refuse(&format!("no server reply: {other:?}"))),
    };
    if reply.len() != 1 + 16 + 65 + 64 || reply[0] != T_SERVER {
        return Err(refuse(&"bad server reply"));
    }
    let ns: [u8; 16] = reply[1..17].try_into().unwrap();
    let es: [u8; 65] = reply[17..82].try_into().unwrap();
    let t = secure::transcript(&creds.id, &nc, &ns, &ec, &es);
    // The engine must prove it is the one we paired with before we answer.
    if !secure::verify(&creds.engine_pub, secure::ROLE_SERVER, &t, &reply[82..]) {
        return Err(refuse(&"engine signature did not verify"));
    }
    let mut auth = vec![T_AUTH];
    auth.extend_from_slice(&secure::sign(&creds.secret, secure::ROLE_DEVICE, &t));
    ws.send(Message::binary(auth)).map_err(|e| refuse(&e))?;
    let peer = secure::parse_public(&es).ok_or_else(|| refuse(&"bad key"))?;
    let chan = Channel::new(&secure::ecdh(&eph, &peer), &nc, &ns, false);
    Ok(Conn { ws, chan })
}

impl Conn {
    pub fn send(&mut self, v: &Value) -> Result<(), String> {
        let f = self
            .chan
            .seal(v.to_string().as_bytes())
            .map_err(|e| format!("{e:?}"))?;
        self.ws.send(Message::binary(f)).map_err(|e| e.to_string())
    }

    /// Next message within `ms`, or `None` (timeout, closed, or an
    /// unauthenticated frame).
    pub fn recv(&mut self, ms: u64) -> Option<Value> {
        self.ws
            .get_mut()
            .set_read_timeout(Some(Duration::from_millis(ms)))
            .ok();
        match self.ws.read() {
            Ok(Message::Binary(b)) => serde_json::from_slice(&self.chan.open(&b).ok()?).ok(),
            _ => None,
        }
    }
}
