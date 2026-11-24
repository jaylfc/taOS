// SPDX-License-Identifier: AGPL-3.0-or-later
// Cross-implementation check: the phone page's own crypto.js (run under node)
// pairs with and talks to the real Rust engine. Usage:
//   node --experimental-websocket interop.cjs <port> <control-socket>
"use strict";
const fs = require("fs");
const net = require("net");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

// Plain HTTP pages have no WebCrypto; prove the page code does not need it.
Object.defineProperty(globalThis.crypto, "subtle", { value: undefined });
const web = path.join(__dirname, "..", "..", "web");
vm.runInThisContext(fs.readFileSync(path.join(web, "vendor", "noble.js"), "utf8"));
const TaosCrypto = require(path.join(web, "crypto.js"));
const [port, control] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;

function ctl(req) {
  return new Promise((resolve, reject) => {
    const s = net.connect(control, () => s.write(JSON.stringify(req) + "\n"));
    let buf = "";
    s.on("data", (d) => { buf += d; if (buf.includes("\n")) { s.end(); resolve(JSON.parse(buf)); } });
    s.on("error", reject);
  });
}

async function call(p, body) {
  const r = await fetch(base + p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!j.ok) { throw new Error(j.error); }
  return j;
}

function open(creds) {
  return new Promise((resolve) => {
    const msgs = [];
    const c = TaosCrypto.connect(`ws://127.0.0.1:${port}/ws`, creds, WebSocket, {
      onopen: () => resolve({ c, msgs, ok: true }),
      onmessage: (m) => msgs.push(m),
      onclose: (why) => resolve({ c, msgs, ok: false, why }),
    });
    setTimeout(() => resolve({ c, msgs, ok: false, why: "timeout" }), 4000);
  });
}

(async () => {
  // A wrong code is refused by the phone side (the engine could not prove the code).
  let code = (await ctl({ op: "pair" })).code;
  const wrong = code === "000000" ? "000001" : "000000";
  await assert.rejects(TaosCrypto.pair(call, wrong, "node"), /not right/);
  // The right code pairs.
  const creds = await TaosCrypto.pair(call, code, "Node phone");
  assert.strictEqual(creds.enginePub.length, 65);
  assert.strictEqual((await ctl({ op: "devices" })).devices.length, 1);
  // A full encrypted round trip with the engine.
  const s = await open(creds);
  assert.ok(s.ok, "session did not open: " + s.why);
  assert.strictEqual(s.msgs[0].t, "hello");
  s.c.send({ t: "ping", n: 41 });
  s.c.send({ t: "ping", n: 42 });
  for (let i = 0; i < 40 && s.msgs.filter((m) => m.t === "pong").length < 2; i++) { await new Promise((r) => setTimeout(r, 50)); }
  assert.deepStrictEqual(s.msgs.filter((m) => m.t === "pong").map((m) => m.n), [41, 42]);
  const st = await ctl({ op: "status" });
  assert.strictEqual(st.devices[0].connected, true);
  s.c.close();
  // The right id with a different device key is not accepted.
  const bad = await open({ id: creds.id, priv: globalThis.TaosNoble.p256.utils.randomPrivateKey(), enginePub: creds.enginePub });
  assert.ok(!bad.ok && !bad.msgs.some((m) => m.t === "hello"), "an unpaired key must not get a session");
  console.log("INTEROP OK");
  process.exit(0);
})().catch((e) => { console.error("INTEROP FAILED", e); process.exit(1); });
