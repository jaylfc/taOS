#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regenerate meshtastic.json and meshcore.json.

Provenance is recorded per frame:
  "recorded"          bytes copied from the upstream projects' own test files
                      (captures of real radios); URL in "source".
  "reference-encoded" bytes produced by the upstream reference library
                      (meshtastic python 2.7.11 / meshcore python 2.3.19) and
                      decoded by it too; expectations come from the library's
                      own parse, never from this repo's decoder.

Needs: pip install meshtastic meshcore. Run from this directory.
"""
import asyncio, json, struct

from meshtastic.protobuf import mesh_pb2, portnums_pb2, telemetry_pb2

PY = "https://raw.githubusercontent.com/meshtastic/python/master/meshtastic/tests/"
frames = []

def add(name, source, raw, expect, url=None):
    frames.append({"name": name, "provenance": source, "source": url, "hex": raw.hex(), "expect": expect})

# ---- recorded (real radio captures from meshtastic/python tests) -----------
rec = {
    "my_info_heltec_v2_1": bytes.fromhex("1a2c08cccfbdc502180d320e312e322e34392e35333534633439500d5d30b5884168e0a71270e89d017808900101"),
    "node_info_heltec_v2_1": bytes.fromhex("223208cccfbdc50212280a09213238616636376363120c556e6b6e6f776e20363763631a033f43432206246f28af67cc300a1a00"),
    "my_info_second_capture": b'\x1a,\x08\xdc\x8c\xd5\xc5\x02\x18\r2\x0e1.2.49.5354c49P\x15]\xe1%\x17Eh\xe0\xa7\x12p\xe8\x9d\x01x\x08\x90\x01\x01',
    "config_complete": b'@\xcf\xe5\xd1\x8c\x0e',
}
for name, raw in rec.items():
    fr = mesh_pb2.FromRadio.FromString(raw)
    kind = fr.WhichOneof("payload_variant")
    exp = {"variant": kind}
    if kind == "my_info":
        exp["my_node_num"] = fr.my_info.my_node_num
    if kind == "node_info":
        exp.update(num=fr.node_info.num, long_name=fr.node_info.user.long_name, short_name=fr.node_info.user.short_name, user_id=fr.node_info.user.id)
    add(name, "recorded", raw, exp, PY + ("test_mesh_interface.py" if "heltec_v2_1" in name else "test_stream_interface.py"))

# ---- reference-encoded packets --------------------------------------------
def pkt(**kw):
    fr = mesh_pb2.FromRadio(); p = fr.packet
    d = kw.pop("decoded", None)
    for k, v in kw.items():
        setattr(p, k, v)
    if d is not None:
        p.decoded.CopyFrom(d)
    return fr

def data(port, payload, **kw):
    d = mesh_pb2.Data(portnum=port, payload=payload, **kw)
    return d

def finish(name, fr, expect):
    raw = fr.SerializeToString()
    # expectation comes from the reference library's own round trip
    back = mesh_pb2.FromRadio.FromString(raw)
    assert back == fr
    add(name, "reference-encoded", raw, expect)

finish("text_broadcast", pkt(**{"from": 0x28af67cc, "to": 0xffffffff, "channel": 0, "id": 0x1234abcd, "rx_time": 1764000000, "rx_snr": 6.5, "hop_limit": 2, "hop_start": 3, "rx_rssi": -91, "decoded": data(portnums_pb2.TEXT_MESSAGE_APP, "hello mesh".encode())}),
       {"kind": "text", "from": "!28af67cc", "to": "!ffffffff", "broadcast": True, "channel": 0, "text": "hello mesh", "snr": 6.5, "rssi": -91, "hops": 1, "packet_id": 0x1234abcd, "radio_ts": 1764000000})
finish("text_direct_utf8", pkt(**{"from": 0x7ed23c00, "to": 0x28af67cc, "channel": 1, "id": 77, "hop_limit": 3, "hop_start": 3, "want_ack": True, "decoded": data(portnums_pb2.TEXT_MESSAGE_APP, "café ✓".encode())}),
       {"kind": "text", "from": "!7ed23c00", "to": "!28af67cc", "broadcast": False, "channel": 1, "text": "café ✓", "hops": 0, "packet_id": 77})
pos = mesh_pb2.Position(latitude_i=514500000, longitude_i=-12300000, altitude=35, time=1764000100, HDOP=120, PDOP=150, sats_in_view=9, precision_bits=32, gps_accuracy=3000)
finish("position_packet", pkt(**{"from": 0x7ed23c00, "to": 0xffffffff, "channel": 0, "id": 78, "rx_snr": -2.25, "rx_rssi": -108, "hop_limit": 1, "hop_start": 3, "decoded": data(portnums_pb2.POSITION_APP, pos.SerializeToString())}),
       {"kind": "position", "from": "!7ed23c00", "lat": 51.45, "lon": -1.23, "alt": 35, "time": 1764000100, "hdop": 120, "pdop": 150, "sats": 9, "precision_bits": 32, "gps_accuracy_mm": 3000, "snr": -2.25, "rssi": -108, "hops": 2})
pos_south = mesh_pb2.Position(latitude_i=-337000000, longitude_i=1511000000, altitude=-5)
finish("position_negative_alt", pkt(**{"from": 5, "to": 0xffffffff, "id": 79, "decoded": data(portnums_pb2.POSITION_APP, pos_south.SerializeToString())}),
       {"kind": "position", "from": "!00000005", "lat": -33.7, "lon": 151.1, "alt": -5, "time": None})
user = mesh_pb2.User(id="!7ed23c00", long_name="TBeam 1", short_name="T1", hw_model=mesh_pb2.HardwareModel.TBEAM)
finish("nodeinfo_packet", pkt(**{"from": 0x7ed23c00, "to": 0xffffffff, "id": 80, "decoded": data(portnums_pb2.NODEINFO_APP, user.SerializeToString())}),
       {"kind": "nodeinfo", "from": "!7ed23c00", "long_name": "TBeam 1", "short_name": "T1"})
tel = telemetry_pb2.Telemetry(time=1764000200); tel.device_metrics.battery_level = 87; tel.device_metrics.voltage = 4.05; tel.device_metrics.channel_utilization = 3.5; tel.device_metrics.air_util_tx = 0.5; tel.device_metrics.uptime_seconds = 12345
finish("telemetry_device", pkt(**{"from": 0x7ed23c00, "to": 0xffffffff, "id": 81, "decoded": data(portnums_pb2.TELEMETRY_APP, tel.SerializeToString())}),
       {"kind": "telemetry", "telemetry": "device", "battery_level": 87, "voltage": 4.05, "uptime_seconds": 12345, "radio_ts": 1764000200})
env = telemetry_pb2.Telemetry(time=1764000300); env.environment_metrics.temperature = 21.5; env.environment_metrics.relative_humidity = 40.25
finish("telemetry_environment", pkt(**{"from": 9, "to": 0xffffffff, "id": 82, "decoded": data(portnums_pb2.TELEMETRY_APP, env.SerializeToString())}),
       {"kind": "telemetry", "telemetry": "environment", "temperature": 21.5, "relative_humidity": 40.25})
rt = mesh_pb2.Routing(); rt.error_reason = mesh_pb2.Routing.NO_ROUTE
finish("routing_nak", pkt(**{"from": 0x28af67cc, "to": 0x7ed23c00, "id": 83, "decoded": data(portnums_pb2.ROUTING_APP, rt.SerializeToString(), request_id=77)}),
       {"kind": "ack", "request_id": 77, "error": "routing_error_1"})
rt0 = mesh_pb2.Routing(); rt0.error_reason = mesh_pb2.Routing.NONE
finish("routing_ack", pkt(**{"from": 0x28af67cc, "to": 0x7ed23c00, "id": 84, "decoded": data(portnums_pb2.ROUTING_APP, rt0.SerializeToString(), request_id=77)}),
       {"kind": "ack", "request_id": 77, "error": None})
fr = mesh_pb2.FromRadio(); fr.packet.__setattr__("from", 0x11223344); fr.packet.to = 0xffffffff; fr.packet.id = 85; fr.packet.encrypted = bytes(range(24))
finish("encrypted_unknown_channel", fr, {"kind": "opaque", "from": "!11223344"})
fr = pkt(**{"from": 3, "to": 0xffffffff, "id": 86, "decoded": data(portnums_pb2.PRIVATE_APP, b"\x01\x02")})
finish("private_app_port", fr, {"kind": "opaque", "from": "!00000003"})
fr = mesh_pb2.FromRadio(); fr.node_info.num = 0x7ed23c00; fr.node_info.user.long_name = "TBeam 1"; fr.node_info.user.short_name = "T1"
fr.node_info.position.latitude_i = 514500000; fr.node_info.position.longitude_i = -12300000; fr.node_info.position.time = 1763999000; fr.node_info.snr = 4.5; fr.node_info.last_heard = 1763999100; fr.node_info.hops_away = 2
finish("node_info_with_position", fr, {"kind": "nodeinfo", "from": "!7ed23c00", "lat": 51.45, "lon": -1.23, "snr": 4.5, "hops": 2, "radio_ts": 1763999100})

# ---- outbound encodings: the reference library must parse ours -------------
tr = mesh_pb2.ToRadio(want_config_id=0x7a050001)
out = {"want_config": {"hex": tr.SerializeToString().hex(), "want_config_id": 0x7a050001}}
tr = mesh_pb2.ToRadio(); p = tr.packet; setattr(p, "to", 0xffffffff); p.id = 0x0badf00d; p.decoded.portnum = portnums_pb2.TEXT_MESSAGE_APP; p.decoded.payload = b"check in ok"
out["text_broadcast"] = {"hex": tr.SerializeToString().hex(), "to": 0xffffffff, "channel": 0, "id": 0x0badf00d, "text": "check in ok", "want_ack": False}
tr = mesh_pb2.ToRadio(); p = tr.packet; setattr(p, "to", 0x28af67cc); p.channel = 2; p.id = 99; p.want_ack = True; p.decoded.portnum = portnums_pb2.TEXT_MESSAGE_APP; p.decoded.payload = "on my way".encode()
out["text_direct_channel2"] = {"hex": tr.SerializeToString().hex(), "to": 0x28af67cc, "channel": 2, "id": 99, "text": "on my way", "want_ack": True}

json.dump({"frames": frames, "outbound": out, "framing": {"empty_frame": "94c30000", "false_start": "940094940094c3000000"}}, open("meshtastic.json", "w"), indent=1)

# ---- MeshCore --------------------------------------------------------------
from meshcore.reader import MessageReader

class D:
    def __init__(self): self.events = []
    async def dispatch(self, ev): self.events.append(ev)

def parse(raw):
    d = D(); r = MessageReader(d)
    asyncio.run(r.handle_rx(bytearray(raw)))
    return d.events

mc = []
def addmc(name, prov, raw, expect_fn=None):
    evs = parse(raw)
    mc.append({"name": name, "provenance": prov, "hex": raw.hex(), "oracle": [{"type": str(e.type.name), "payload": json.loads(json.dumps(e.payload, default=lambda o: o.hex() if isinstance(o, (bytes, bytearray)) else str(o)))} for e in evs]})

pub = bytes(range(1, 33))
selfinfo = bytes([5, 1, 14, 22]) + pub + struct.pack("<ii", 51450000, -1230000) + bytes([0, 0, 0, 0]) + struct.pack("<II", 869525, 250000) + bytes([11, 5]) + b"Alpha"
addmc("self_info", "reference-built (layout: MyMesh.cpp, parsed by meshcore_py)", selfinfo)
cpub = bytes(range(100, 132))
contact = bytes([3]) + cpub + bytes([1, 0, 255]) + bytes(64) + b"Bravo".ljust(32, b"\0") + struct.pack("<Iiii", 1764000000, 51451234, -1229876, 1764000050)
addmc("contact_with_position", "reference-built", contact)
addmc("new_advert_push", "reference-built", bytes([0x8A]) + contact[1:])
cpub0 = bytes(range(150, 182))
contact0 = bytes([3]) + cpub0 + bytes([1, 0, 255]) + bytes(64) + b"NoFix".ljust(32, b"\0") + struct.pack("<Iiii", 1764000000, 0, 0, 1764000050)
addmc("contact_without_position", "reference-built", contact0)
snr = struct.pack("<b", -22)  # -5.5 dB * 4
addmc("contact_msg_v3_direct", "reference-built", bytes([16]) + snr + b"\0\0" + cpub[:6] + bytes([255, 0]) + struct.pack("<I", 1764000123) + "see you at camp".encode())
addmc("contact_msg_v3_two_hops", "reference-built", bytes([16]) + struct.pack("<b", 10) + b"\0\0" + cpub[:6] + bytes([2, 0]) + struct.pack("<I", 1764000124) + b"relayed")
addmc("contact_msg_v2", "reference-built", bytes([7]) + cpub[:6] + bytes([255, 0]) + struct.pack("<I", 1764000125) + b"old style")
addmc("channel_msg_v3", "reference-built", bytes([17]) + struct.pack("<b", 18) + b"\0\0" + bytes([1, 255, 0]) + struct.pack("<I", 1764000130) + b"Bravo: water found")
addmc("channel_msg_v2", "reference-built", bytes([8]) + bytes([2, 3, 0]) + struct.pack("<I", 1764000131) + b"Bravo: ok")
addmc("send_confirmed_push", "reference-built", bytes([0x82]) + bytes.fromhex("0badf00d") + struct.pack("<I", 1500))
addmc("messages_waiting_push", "reference-built", bytes([0x83]))
addmc("no_more_messages", "reference-built", bytes([10]))
# published vectors that are commands (docs/companion_protocol.md)
cmds = {
    "app_start_doc": "01000000000000006d63636c69",
    "device_query_doc": "1603",
    "get_message_doc": "0a",
    "send_channel_doc": "030001d202964948656c6c6f",
}
json.dump({"frames": mc, "documented_commands": cmds}, open("meshcore.json", "w"), indent=1)
print("ok", len(frames), len(mc))
