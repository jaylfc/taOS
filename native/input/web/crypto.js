// SPDX-License-Identifier: AGPL-3.0-or-later
// Pairing and the encrypted session for the phone page. Works in a browser
// (globals TaosNoble, crypto) and under node (for the interop test).
// The protocol is documented in native/input/README.md; secure.rs and pake.rs
// are the engine side of every step here.
(function (root, factory) {
  if (typeof module === "object" && module.exports) { module.exports = factory(root.TaosNoble, globalThis.crypto); }
  else { root.TaosCrypto = factory(root.TaosNoble, root.crypto); }
})(typeof self !== "undefined" ? self : globalThis, function (Noble, webcrypto) {
  "use strict";
  // crypto.subtle only exists in secure contexts (https or localhost), and this
  // page is served over plain HTTP on the home network, so every primitive here
  // comes from the audited, dependency free noble libraries (vendor/noble.js).
  // Only the random number generator is the browser's own (available everywhere).
  var N = Noble, enc = new TextEncoder();

  function utf8(s) { return enc.encode(s); }
  function concat() {
    var n = 0, i, a;
    for (i = 0; i < arguments.length; i++) { n += arguments[i].length; }
    var out = new Uint8Array(n); n = 0;
    for (i = 0; i < arguments.length; i++) { a = arguments[i]; out.set(a, n); n += a.length; }
    return out;
  }
  function u32be(n) { return new Uint8Array([(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255]); }
  function u64be(n) { var b = new Uint8Array(8); new DataView(b.buffer).setBigUint64(0, BigInt(n)); return b; }
  function hex(b) { var s = ""; for (var i = 0; i < b.length; i++) { s += (b[i] < 16 ? "0" : "") + b[i].toString(16); } return s; }
  function unhex(s) {
    if (typeof s !== "string" || s.length % 2) { throw new Error("bad hex"); }
    var out = new Uint8Array(s.length / 2);
    for (var i = 0; i < out.length; i++) { out[i] = parseInt(s.substr(2 * i, 2), 16); if (isNaN(out[i])) { throw new Error("bad hex"); } }
    return out;
  }
  function rand(n) { var b = new Uint8Array(n); webcrypto.getRandomValues(b); return b; }
  function ctEq(a, b) { if (a.length !== b.length) { return false; } var d = 0; for (var i = 0; i < a.length; i++) { d |= a[i] ^ b[i]; } return d === 0; }
  function hkdf(ikm, salt, info, bytes) { return N.hkdf(N.sha256, ikm, salt, utf8(info), bytes); }

  // ---- pairing: CPace style PAKE on ristretto255 (see pake.rs) ----
  function generator(code, sid) {
    var c = utf8(code);
    var wide = N.sha512(concat(utf8("taos-input/cpace-v1/generator"), u32be(c.length), c, sid));
    return N.RistrettoPoint.hashToCurve(wide);
  }
  function randomScalar() {
    for (;;) {
      var b = rand(64), n = 0n;
      for (var i = b.length - 1; i >= 0; i--) { n = (n << 8n) | BigInt(b[i]); } // little endian, like from_bytes_mod_order_wide
      n = n % N.ORDER;
      if (n !== 0n) { return n; }
    }
  }
  function point(bytes) {
    var p = N.RistrettoPoint.fromHex(hex(bytes));
    if (p.equals(N.RistrettoPoint.ZERO)) { throw new Error("bad point"); }
    return p;
  }
  function pakeKeys(k, sid, ya, yb) {
    if (k.equals(N.RistrettoPoint.ZERO)) { throw new Error("bad point"); }
    var transcript = N.sha256(concat(utf8("taos-input/cpace-v1/transcript"), sid, ya, yb));
    var kb = k.toRawBytes();
    var label = function (l) { return hkdf(kb, transcript, l, 32); };
    return {
      confirmS: N.hmac(N.sha256, label("confirm-s"), transcript),
      confirmC: N.hmac(N.sha256, label("confirm-c"), transcript),
      encC2S: label("enc-c2s"),
      encS2C: label("enc-s2c")
    };
  }
  // Each pairing key seals exactly one message, so the zero nonce is safe.
  function gcmOnce(key, aad, data, decrypt) {
    var c = N.gcm(key, new Uint8Array(12), aad);
    return decrypt ? c.decrypt(data) : c.encrypt(data);
  }

  /**
   * Pair this phone. `call(path, obj)` posts JSON and resolves the parsed
   * reply (rejecting with Error(message) on a refusal). Resolves the
   * credentials to keep. The device private key is generated here and never
   * sent anywhere. (On a secure origin it could be a non-extractable
   * CryptoKey; on plain HTTP WebCrypto does not exist, so it is raw bytes in
   * this origin's IndexedDB.)
   */
  async function pair(call, code, name) {
    var begin = await call("/api/pair/begin", {});
    var sid = unhex(begin.sid);
    var s = randomScalar();
    var ya = generator(code, sid).multiply(s).toRawBytes();
    var init = await call("/api/pair/init", { pid: begin.pid, ya: hex(ya) });
    var yb = unhex(init.yb);
    var keys = pakeKeys(point(yb).multiply(s), sid, ya, yb);
    if (!ctEq(keys.confirmS, unhex(init.confirm))) { throw new Error("That code is not right."); }
    var priv = N.p256.utils.randomPrivateKey();
    var pub = N.p256.getPublicKey(priv, false);
    var sealed = gcmOnce(keys.encC2S, utf8("taos-input/pair/device"), concat(pub, utf8(name || "Phone")), false);
    var fin = await call("/api/pair/finish", { pid: begin.pid, confirm: hex(keys.confirmC), blob: hex(sealed) });
    var enginePub = gcmOnce(keys.encS2C, utf8("taos-input/pair/engine"), unhex(fin.blob), true);
    if (enginePub.length !== 65) { throw new Error("bad engine key"); }
    return { id: fin.id, priv: priv, pub: pub, enginePub: enginePub };
  }

  // ---- session ----

  /**
   * Open an authenticated, encrypted session. creds = {id (hex), priv, enginePub}.
   * Handlers: onopen(helloMsg), onmessage(obj), onclose(reason). Returns {send(obj), close(), isOpen()}.
   */
  function connect(url, creds, WS, handlers) {
    var ws = new WS(url, ["taos-input.v2"]);
    ws.binaryType = "arraybuffer";
    var stage = "hello", closed = false;
    var id = unhex(creds.id), eph = N.p256.utils.randomPrivateKey(), ec = N.p256.getPublicKey(eph, false), nc = rand(16);
    var ns, es, transcript, kSend, kRecv, sendCtr = 0n, recvCtr = 0n;

    function fail(why) { if (!closed) { closed = true; try { ws.close(); } catch (e) { /* closing anyway */ } if (handlers.onclose) { handlers.onclose(why); } } }
    function label(role) { return concat(utf8("taos-input/v2/" + role), new Uint8Array([0]), transcript); }

    ws.onopen = function () { ws.send(concat(new Uint8Array([1]), id, nc, ec)); };

    function onServer(b) {
      if (b.length !== 1 + 16 + 65 + 64 || b[0] !== 2) { throw new Error("bad server frame"); }
      ns = b.slice(1, 17); es = b.slice(17, 82);
      transcript = concat(id, nc, ns, ec, es);
      // The engine must prove it is the one this phone paired with before we answer.
      if (!N.p256.verify(b.slice(82), N.sha256(label("server")), creds.enginePub, { lowS: false })) {
        throw new Error("the engine is not the one this phone paired with");
      }
      var sig = N.p256.sign(N.sha256(label("device")), creds.priv).toCompactRawBytes();
      var shared = N.p256.getSharedSecret(eph, es, true).slice(1); // x coordinate
      var okm = hkdf(shared, N.sha256(concat(nc, ns)), "taos-input/v2/keys", 64);
      kSend = okm.slice(0, 32); kRecv = okm.slice(32);
      stage = "open";
      ws.send(concat(new Uint8Array([3]), sig));
    }

    function onData(b) {
      if (b.length < 9 + 16 || b[0] !== 0x10) { throw new Error("bad frame"); }
      var ctr = new DataView(b.buffer, b.byteOffset, b.byteLength).getBigUint64(1);
      if (ctr !== recvCtr) { throw new Error("frame out of order"); }
      var pt = N.gcm(kRecv, concat(u32be(1), b.slice(1, 9)), b.slice(0, 9)).decrypt(b.slice(9));
      recvCtr++;
      var msg = JSON.parse(new TextDecoder().decode(pt));
      if (msg.t === "hello" && handlers.onopen) { handlers.onopen(msg); }
      if (handlers.onmessage) { handlers.onmessage(msg); }
    }

    ws.onmessage = function (ev) {
      if (!(ev.data instanceof ArrayBuffer)) { fail("unexpected text frame"); return; }
      try { var b = new Uint8Array(ev.data); if (stage === "hello") { onServer(b); } else { onData(b); } }
      catch (e) { fail(String(e && e.message || e)); }
    };
    ws.onclose = function () { fail("closed"); };
    ws.onerror = function () { /* onclose follows */ };

    return {
      ws: ws,
      isOpen: function () { return stage === "open" && !closed && ws.readyState === 1; },
      send: function (obj) {
        if (stage !== "open" || closed) { return false; }
        var head = concat(new Uint8Array([0x10]), u64be(sendCtr)), data = utf8(JSON.stringify(obj));
        var ct = N.gcm(kSend, concat(u32be(0), head.slice(1)), head).encrypt(data);
        sendCtr++;
        ws.send(concat(head, ct));
        return true;
      },
      close: function () { closed = true; try { ws.close(); } catch (e) { /* ignore */ } }
    };
  }

  return { pair: pair, connect: connect, hex: hex, unhex: unhex };
});
