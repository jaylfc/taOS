"""Sections 7, 8 and 11.2: notification mirroring (N*), clipboard (C*), the phone lock
screen (L*). xfail(strict=True) until the PR in each marker; red and control together.
"""
from __future__ import annotations

from _device_link_bed import Counter, expect, needs
from _device_link_flows import (
    console_lock, enrol_first_device, enrol_later_device, inject_lock_state, plaintext_tap, device_id, MY,
)

NOTIF = "/api/notifications"
CANARY_T, CANARY_B = "CANARY-T", "CANARY-B"


def pair(bed):
    a1, ap = bed.at("a-host1"), bed.at("a-phone")
    enrol_first_device(bed, a1)
    enrol_later_device(bed, ap, a1)
    return a1, ap


def notify(bed, ep, title="hello", body="world", app="fdo:test-app"):
    return expect(bed.local(ep, "POST", "/api/notifications/os", {"app": app, "title": title, "body": body}), 200)


def rows(bed, ep):
    return expect(bed.local(ep, "GET", NOTIF), 200).json["items"]


def mirror_to(bed, src, dst, on=True, app="fdo:test-app"):
    expect(bed.local(src, "PUT", f"{MY}/{device_id(bed, dst)}/mirroring", {"send": on, "apps": {app: on}}), 200)
    expect(bed.local(dst, "PUT", f"{MY}/{device_id(bed, src)}/mirroring", {"receive": on}), 200)


def outbox_log(bed, ep):
    return bed.exec(ep.node, "cat", "/var/log/bed/controller.log").stdout


# -- notification mirroring ----------------------------------------------
@needs("T5")
def test_N1_opt_in_off(bed):
    a1, ap = pair(bed)
    notify(bed, ap)
    bed.sleep(30)
    assert [r for r in rows(bed, a1) if r["title"] == "hello"] == []         # nothing arrived
    assert "not sent: send off" in outbox_log(bed, ap)                       # and that was measured, not assumed
    mirror_to(bed, ap, a1)                                                   # control: toggles on -> the row arrives with the device label
    notify(bed, ap, title="hello2")
    bed.sleep(30)
    assert any(r["title"] == "hello2" and r.get("device_label") for r in rows(bed, a1))


@needs("T5")
def test_N2_per_app(bed):
    a1, ap = pair(bed)
    mirror_to(bed, ap, a1, on=True, app="taos:agents")
    notify(bed, ap, title="off-app", app="fdo:test-app")
    bed.sleep(30)
    assert not [r for r in rows(bed, a1) if r["title"] == "off-app"]         # red: fdo:test-app off
    notify(bed, ap, title="on-app", app="taos:agents")                       # control: taos:agents on
    bed.sleep(30)
    assert [r for r in rows(bed, a1) if r["title"] == "on-app"]


@needs("T5")
def test_N3_mirrored_rows_do_not_loop(bed):
    a1, ap = pair(bed)
    mirror_to(bed, ap, a1)
    mirror_to(bed, a1, ap)
    notify(bed, ap, title="once")
    bed.sleep(60)
    assert len([r for r in rows(bed, ap) if r["title"] == "once"]) == 1      # control: the original row exists once
    assert [r for r in rows(bed, ap) if r.get("origin_device") and r["title"] == "once"] == []   # red: never sent back


@needs("T5")
def test_N4_webhook_not_called_for_mirrored_rows(bed):
    a1, ap = pair(bed)
    mirror_to(bed, ap, a1)
    hook = Counter(lambda: int(bed.exec(a1.node, "sh", "-c", "cat /tmp/webhook-hits 2>/dev/null || echo 0").stdout))
    notify(bed, ap, title="mirrored")
    bed.sleep(30)
    assert hook.delta() == 0                                                 # red: 0 calls for a mirrored row
    notify(bed, a1, title="local")
    assert hook.delta() == 1                                                 # control: 1 call for a local row, same receiver


@needs("T5")
def test_N5_dismiss_sync(bed):
    a1, ap = pair(bed)
    a2 = bed.at("a-host2")
    enrol_later_device(bed, a2, a1)
    for src, dst in ((ap, a1), (ap, a2)):
        mirror_to(bed, src, dst)
    notify(bed, ap, title="dismiss-me")
    bed.sleep(30)
    row = next(r for r in rows(bed, a1) if r["title"] == "dismiss-me")
    expect(bed.local(a1, "POST", f"{NOTIF}/{row['id']}/dismiss"), 200)
    bed.sleep(30)
    for ep in (ap, a2):                                                      # red: origin archived, then the third copy
        assert next(r for r in rows(bed, ep) if r["title"] == "dismiss-me")["archived"]
    notify(bed, ap, title="consent", app="taos:agents")                      # control: a pinned consent row is only marked read
    pinned = next(r for r in rows(bed, ap) if r["title"] == "consent")
    expect(bed.local(a1, "POST", f"{NOTIF}/{pinned['id']}/dismiss"), 200)
    assert not next(r for r in rows(bed, ap) if r["title"] == "consent")["archived"]


@needs("T5")
def test_N6_outbox(bed):
    a1, ap = pair(bed)
    mirror_to(bed, ap, a1)
    bed.stop(a1.node)
    notify(bed, ap, title="held")
    bed.sleep(600)
    bed.start(a1.node)
    bed.sleep(60)
    assert len([r for r in rows(bed, a1) if r["title"] == "held"]) == 1      # held, then delivered once
    bed.stop(a1.node)
    notify(bed, ap, title="too-late")
    bed.sleep(24 * 3600 + 60)
    bed.start(a1.node)
    bed.sleep(60)
    assert not [r for r in rows(bed, a1) if r["title"] == "too-late"]        # red: dropped at 24 h + 1


@needs("T5")
def test_N7_rate_limit(bed):
    a1, ap = pair(bed)
    mirror_to(bed, ap, a1)
    for i in range(31):
        notify(bed, ap, title=f"burst-{i}")
    bed.sleep(30)
    got = [r["title"] for r in rows(bed, a1)]
    assert sum(t.startswith("burst-") for t in got) == 30                    # control: the first 30 are individual
    assert any("more" in t for t in got)                                     # red: the 31st collapsed into "N more"


@needs("T5")
def test_N8_sealing(bed):
    a1, ap = pair(bed)
    mirror_to(bed, ap, a1)
    tap = plaintext_tap(bed, a1)
    notify(bed, ap, title="SEALED-TITLE")
    bed.sleep(30)
    assert "SEALED-TITLE" not in tap.read()                                  # red: no title text in the envelope
    assert any(r["title"] == "SEALED-TITLE" for r in rows(bed, a1))          # control: the receiver decrypted it


@needs("T6")
def test_N9_kiosk_duplicate(bed):
    ap = bed.at("a-phone")
    bed.exec(ap.node, "notify-send", "from-os-app")
    assert len([r for r in rows(bed, ap) if r["title"] == "from-os-app"]) == 1        # control: one OS row
    bed.exec(ap.node, "sh", "-c", "chromium-notify 'web-note'")
    assert len([r for r in rows(bed, ap) if r["title"] == "web-note"]) == 1           # red: one row, not two


# -- clipboard -----------------------------------------------------------
def send_clip(bed, src, dst, text, hint=None):
    body = {"text": text}
    if hint:
        body["hint"] = hint
    return bed.local(src, "POST", f"{MY}/{device_id(bed, dst)}/clipboard", body)


@needs("T7")
def test_C1_secret_is_refused(bed):
    a1, ap = pair(bed)
    helper = Counter(lambda: int(bed.exec(ap.node, "sh", "-c", "cat /tmp/clipboard-events 2>/dev/null || echo 0").stdout))
    expect(send_clip(bed, a1, ap, "hunter2", hint="x-kde-passwordManagerHint=secret"), 422)
    assert helper.delta() == 0
    expect(send_clip(bed, a1, ap, "plain text"), 200)                        # control
    assert helper.delta() == 1


@needs("T7")
def test_C2_not_on_disk(bed):
    a1, ap = pair(bed)
    helper = Counter(lambda: int(bed.exec(ap.node, "sh", "-c", "cat /tmp/clipboard-events 2>/dev/null || echo 0").stdout))
    expect(send_clip(bed, a1, ap, "CLIPBOARD-CANARY-0001"), 200)
    assert helper.delta() == 1                                               # control: the canary was received
    for ep in (a1, ap):                                                      # red: 0 hits in data dirs and logs
        r = bed.exec(ep.node, "sh", "-c", "grep -r CLIPBOARD-CANARY-0001 /data /var/log 2>/dev/null | wc -l")
        assert r.stdout.strip() == "0"


@needs("T7")
def test_C3_size_limit(bed):
    a1, ap = pair(bed)
    expect(send_clip(bed, a1, ap, "x" * (64 * 1024 + 1)), 413)               # red: 64 KiB + 1
    expect(send_clip(bed, a1, ap, "x" * (64 * 1024)), 200)                   # control: 64 KiB


# -- phone lock screen ---------------------------------------------------
LOCK = f"{MY}/lock-screen"


def lock_summary(bed, ep):
    return bed.local(ep, "GET", "/auth/lock-mirror-summary", headers={"X-taOS-Console": "1"})


def set_level(bed, ep, level):
    return bed.local(ep, "PUT", LOCK, {"level": level})


def receive_canary(bed, a1, ap, t=CANARY_T, b=CANARY_B):
    mirror_to(bed, a1, ap)
    notify(bed, a1, title=t, body=b)
    bed.sleep(30)


@needs("T5")
def test_L0_default_level_is_summary(bed):
    a1, ap = pair(bed)
    assert expect(bed.local(ap, "GET", LOCK), 200).json["level"] == "summary"          # a never-touched phone
    expect(set_level(bed, ap, "full"), 200)                                  # control: the setting is read, not hard-coded
    assert bed.local(ap, "GET", LOCK).json["level"] == "full"


@needs("T5")
def test_L1s_summary_hides_content_while_locked(bed):
    a1, ap = pair(bed)
    receive_canary(bed, a1, ap)
    console_lock(bed, ap, locked=True)
    body = expect(lock_summary(bed, ap), 200)
    assert CANARY_T not in body.body and CANARY_B not in body.body           # red
    assert body.json["count"] == 1 and body.json["apps"]                     # control: count and app name are there
    console_lock(bed, ap, locked=False)
    listing = str(rows(bed, ap))
    assert CANARY_T in listing and CANARY_B in listing                       # after unlock both canaries show


@needs("T5")
def test_L1f_full_shows_only_the_current_session(bed):
    a1, ap = pair(bed)
    expect(set_level(bed, ap, "full"), 200)
    receive_canary(bed, a1, ap, t="OLD-T", b="OLD-B")
    console_lock(bed, ap, locked=True)
    console_lock(bed, ap, locked=False)
    receive_canary(bed, a1, ap)
    console_lock(bed, ap, locked=True)
    body = expect(lock_summary(bed, ap), 200).body
    assert "OLD-T" not in body                                               # red: an older session's canary is absent
    assert CANARY_T in body and CANARY_B in body                             # control: this session's are present


@needs("T5")
def test_L1h_hidden_shows_nothing(bed):
    a1, ap = pair(bed)
    expect(set_level(bed, ap, "hidden"), 200)
    receive_canary(bed, a1, ap)
    console_lock(bed, ap, locked=True)
    r = expect(lock_summary(bed, ap), 200)
    assert set(r.json) == {"session", "level"}                               # red: exactly {session, level}
    assert CANARY_T not in r.body and "test-app" not in r.body
    console_lock(bed, ap, locked=False)
    assert CANARY_T in str(rows(bed, ap))                                    # control: stored, only not shown


@needs("T5")
def test_L2_shape_per_level(bed):
    a1, ap = pair(bed)
    shapes = {"summary": {"session", "level", "count", "apps"}, "full": {"session", "level", "count", "apps", "groups"},
              "hidden": {"session", "level"}}
    for level, keys in shapes.items():
        expect(set_level(bed, ap, level), 200)
        r = expect(lock_summary(bed, ap), 200)                               # control: 200 to the console caller
        assert set(r.json) == keys, (level, set(r.json) ^ keys)              # red: any extra or missing key fails


@needs("T5")
def test_L3_summary_route_is_console_only(bed):
    a1, ap = pair(bed)
    expect(bed.link(a1, ap, "GET", "/auth/lock-mirror-summary"), 404)        # red: non-loopback caller
    expect(bed.local(ap, "GET", "/auth/lock-mirror-summary"), 404)           # red: no X-taOS-Console header
    expect(lock_summary(bed, ap), 200)                                       # control: the console caller


@needs("T5")
def test_L4_session_keying(bed):
    a1, ap = pair(bed)
    mirror_to(bed, a1, ap)
    console_lock(bed, ap, locked=True)
    notify(bed, a1, title="s1")
    bed.sleep(30)
    assert lock_summary(bed, ap).json["count"] == 1
    notify(bed, a1, title="s1b")
    bed.sleep(30)
    assert lock_summary(bed, ap).json["count"] == 2                          # control: two in one locked session
    console_lock(bed, ap, locked=False)
    console_lock(bed, ap, locked=True)
    notify(bed, a1, title="s2")
    bed.sleep(30)
    assert lock_summary(bed, ap).json["count"] == 1                          # red: a new lock session starts at 1, not 3


@needs("T4")
def test_L5_vouch_and_remove_need_the_phone_unlocked(bed):
    a1, ap = pair(bed)
    a2 = bed.at("a-host2")
    from _device_link_flows import request_join, approve, remove_device
    j = expect(request_join(bed, a2), 200).json
    console_lock(bed, ap, locked=True)
    expect(approve(bed, ap, j["request_id"], j["pairing_secret"]), 423, "device_locked")
    expect(remove_device(bed, ap, device_id(bed, a1)), 423, "device_locked")
    console_lock(bed, ap, locked=False)                                      # control: the same calls after unlock
    expect(approve(bed, ap, j["request_id"], j["pairing_secret"]), 200)


@needs("T4")
def test_L6_a_remote_session_does_not_unlock(bed):
    a1, ap = pair(bed)
    a2 = bed.at("a-host2")
    from _device_link_flows import request_join, approve
    j = expect(request_join(bed, a2), 200).json
    console_lock(bed, ap, locked=True)
    expect(approve(bed, ap, j["request_id"], j["pairing_secret"]), 423)      # red: remote admin session, console locked
    console_lock(bed, ap, locked=False)
    expect(approve(bed, ap, j["request_id"], j["pairing_secret"]), 200)      # control: console unlocked, same session


@needs("T7")
def test_L7_clipboard_needs_the_phone_unlocked(bed):
    a1, ap = pair(bed)
    helper = Counter(lambda: int(bed.exec(ap.node, "sh", "-c", "cat /tmp/clipboard-events 2>/dev/null || echo 0").stdout))
    console_lock(bed, ap, locked=True)
    expect(send_clip(bed, a1, ap, "x"), 423, "device_locked")
    expect(send_clip(bed, ap, a1, "x"), 423, "device_locked")
    assert helper.delta() == 0
    console_lock(bed, ap, locked=False)                                      # control: delivered in both directions
    expect(send_clip(bed, a1, ap, "x"), 200)
    expect(send_clip(bed, ap, a1, "x"), 200)


@needs("T6")
def test_L8_fail_closed(bed):
    a1, ap = pair(bed)
    # a lock-state answer with an extra key, or a timeout, is treated as locked
    inject_lock_state(bed, ap, '{"locked":false,"session":1,"extra":1}')
    assert bed.local(ap, "GET", "/api/notifications/popup-level").json["effective_lock"] == "locked"   # red
    inject_lock_state(bed, ap, '{"locked":false,"session":1}')
    assert bed.local(ap, "GET", "/api/notifications/popup-level").json["effective_lock"] == "unlocked"  # control


@needs("T5")
def test_L9_setting_change_needs_unlock(bed):
    a1, ap = pair(bed)
    console_lock(bed, ap, locked=True)
    expect(set_level(bed, ap, "full"), 423)                                  # red
    console_lock(bed, ap, locked=False)
    assert bed.local(ap, "GET", LOCK).json["level"] == "summary"             # unchanged
    expect(set_level(bed, ap, "full"), 200)                                  # control
    assert bed.local(ap, "GET", LOCK).json["level"] == "full"
